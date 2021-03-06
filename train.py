from pathlib import PurePath
from typing import Callable

import torch
import torch.nn as nn
from labml import lab, experiment, tracker, monit, logger
from labml.configs import option
from labml.logger import Text
from labml.utils.pytorch import get_modules

from labml_helpers.datasets.text import TextDataset, SequentialDataLoader
from labml_helpers.device import DeviceConfigs
from labml_helpers.module import Module
from labml_helpers.optimizer import OptimizerConfigs
from labml_helpers.train_valid import TrainValidConfigs, Mode
from labml_nn.transformers import TransformerConfigs


class SourceCodeDataset(TextDataset):
    def __init__(self, path: PurePath, tokenizer: Callable):
        with monit.section("Load data"):
            train = self.load(path / 'train.py')
            valid = self.load(path / 'valid.py')

        super().__init__(path, tokenizer, train, valid, '')


class Configs(TrainValidConfigs):
    device = DeviceConfigs()
    model: Module
    text: TextDataset
    batch_size: int = 16
    seq_len: int = 512
    n_tokens: int
    n_layers: int = 2
    dropout: float = 0.2
    d_model: int = 512
    rnn_size: int = 512
    rhn_depth: int = 1
    tokenizer: Callable
    inner_iterations = 100

    is_save_models = True

    transformer: TransformerConfigs

    def run(self):
        for _ in self.training_loop:
            prompt = 'def train('
            log = [(prompt, Text.subtle)]
            for i in monit.iterate('Sample', 25):
                data = self.text.text_to_i(prompt).unsqueeze(-1)
                data = data.to(self.device)
                output, *_ = self.model(data)
                output = output.argmax(dim=-1).squeeze()
                prompt += '' + self.text.itos[output[-1]]
                log += [('' + self.text.itos[output[-1]], Text.value)]

            logger.log(log)

            self.run_step()


class SimpleAccuracyFunc(Module):
    def __call__(self, output: torch.Tensor, target: torch.Tensor) -> int:
        pred = output.argmax(dim=-1)
        return pred.eq(target).sum().item() / target.shape[1]


@option(Configs.accuracy_func)
def simple_accuracy():
    return SimpleAccuracyFunc()


@option(Configs.transformer)
def default_transformer(c: Configs):
    conf = TransformerConfigs()
    conf.d_model = c.d_model
    conf.n_layers = c.n_layers
    conf.n_src_vocab = c.n_tokens
    conf.n_tgt_vocab = c.n_tokens
    conf.dropout = c.dropout

    return conf


@option(Configs.optimizer)
def _optimizer(c: Configs):
    optimizer = OptimizerConfigs()
    optimizer.parameters = c.model.parameters()
    optimizer.optimizer = 'Adam'
    optimizer.d_model = c.d_model

    return optimizer


class CrossEntropyLoss(Module):
    def __init__(self, n_tokens: int):
        super().__init__()
        self.n_tokens = n_tokens
        self.loss = nn.CrossEntropyLoss()

    def __call__(self, outputs, targets):
        return self.loss(outputs.view(-1, self.n_tokens), targets.view(-1))


@option(Configs.loss_func)
def _loss_func(c: Configs):
    return CrossEntropyLoss(c.n_tokens)


@option(Configs.n_tokens)
def _n_tokens(c: Configs):
    return c.text.n_tokens


@option(Configs.model)
def lstm_model(c: Configs):
    from models.lstm import LstmModel
    m = LstmModel(n_tokens=c.n_tokens,
                  embedding_size=c.d_model,
                  hidden_size=c.rnn_size,
                  n_layers=c.n_layers)
    return m.to(c.device)


@option(Configs.model)
def rhn_model(c: Configs):
    from models.highway import RhnModel
    m = RhnModel(n_tokens=c.n_tokens,
                 embedding_size=c.d_model,
                 hidden_size=c.rnn_size,
                 n_layers=c.n_layers,
                 depth=c.rhn_depth)
    return m.to(c.device)


@option(Configs.model)
def transformer_model(c: Configs):
    from models.transformer import TransformerModel
    m = TransformerModel(n_tokens=c.n_tokens,
                         d_model=c.d_model,
                         encoder=c.transformer.encoder,
                         src_embed=c.transformer.src_embed)

    return m.to(c.device)


def character_tokenizer(x: str):
    return list(x)


@option(Configs.tokenizer)
def character():
    return character_tokenizer


@option(Configs.text)
def source_code(c: Configs):
    return SourceCodeDataset(lab.get_data_path(), c.tokenizer)


@option(Configs.train_loader)
def train_loader(c: Configs):
    return SequentialDataLoader(text=c.text.train,
                                dataset=c.text,
                                batch_size=c.batch_size,
                                seq_len=c.seq_len)


@option(Configs.valid_loader)
def train_loader(c: Configs):
    return SequentialDataLoader(text=c.text.valid,
                                dataset=c.text,
                                batch_size=c.batch_size,
                                seq_len=c.seq_len)


def main():
    conf = Configs()
    conf.n_layers = 2
    conf.batch_size = 2
    conf.epochs = 32
    # Assign one of transformer_mode, lstm_model, or rhn_model
    conf.model = 'lstm_model'
    experiment.create(name="source_code",
                      comment='lstm model')
    experiment.configs(conf, {
        'optimizer.optimizer': 'Adam',
        'optimizer.learning_rate': 2.5e-4,
        'device.cuda_device': 1
    }, 'run')
    experiment.add_pytorch_models(get_modules(conf))
    # experiment.load('d5ba7f56d88911eaa6629b54a83956dc')
    with experiment.start():
        conf.run()


if __name__ == '__main__':
    main()
