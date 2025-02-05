import sys
from typing import Any, Dict, Optional, Union

import keras_core as keras
import wandb
from keras_core.callbacks import Callback
from wandb.sdk.lib import telemetry

tf_backend_available = False
torch_backend_available = False
jax_backend_available = False

if keras.backend.backend() == "tensorflow":
    import tensorflow as tf

    tf_backend_available = True
elif keras.backend.backend() == "torch":
    import torch

    torch_backend_available = True
elif keras.backend.backend() == "jax":
    import jax
    import numpy as np

    jax_backend_available = True

if sys.version_info >= (3, 8):
    from typing import Literal
else:
    from typing_extensions import Literal


LogStrategy = Literal["epoch", "batch"]


class WandbMetricsLogger(Callback):
    """Logger that sends system metrics to W&B.

    `WandbMetricsLogger` automatically logs the `logs` dictionary that callback methods
    take as argument to wandb.

    !!! note "This callback automatically logs the following to a W&B run page"
        - system (CPU/GPU/TPU) metrics
        - train and validation metrics defined in `model.compile`,
        - learning rate (both for a fixed value or a learning rate scheduler)

    !!! note "Notes"
        If you resume training by passing `initial_epoch` to `model.fit` and you are
        using a learning rate scheduler, make sure to pass `initial_global_step` to
        `WandbMetricsLogger`. The `initial_global_step` is `step_size * initial_step`,
        where `step_size` is number of training steps per epoch. `step_size` can be
        calculated as the product of the cardinality of the training dataset and the
        batch size.

    !!! example "Example notebooks:"
        - [Image Classification using Keras Core](../examples/image_classification).

    Arguments:
        log_freq (Union[LogStrategy, int]): ("epoch", "batch", or int) if "epoch",
            logs metrics at the end of each epoch. If "batch", logs metrics at the end
            of each batch. If an integer, logs metrics at the end of that
            many batches. Defaults to "epoch".
        initial_global_step (int): Use this argument to correcly log the
            learning rate when you resume training from some `initial_epoch`,
            and a learning rate scheduler is used. This can be computed as
            `step_size * initial_step`. Defaults to 0.
    """

    def __init__(
        self,
        log_freq: Union[LogStrategy, int] = "epoch",
        initial_global_step: int = 0,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        if wandb.run is None:
            raise wandb.Error(
                "You must call `wandb.init()` before WandbMetricsLogger()"
            )

        with telemetry.context(run=wandb.run) as tel:
            tel.feature.keras_metrics_logger = True

        if log_freq == "batch":
            log_freq = 1

        self.logging_batch_wise = isinstance(log_freq, int)
        self.log_freq: Any = log_freq if self.logging_batch_wise else None
        self.global_batch = 0
        self.global_step = initial_global_step

        if self.logging_batch_wise:
            # define custom x-axis for batch logging.
            wandb.define_metric("batch/batch_step")
            # set all batch metrics to be logged against batch_step.
            wandb.define_metric("batch/*", step_metric="batch/batch_step")
        else:
            # define custom x-axis for epoch-wise logging.
            wandb.define_metric("epoch/epoch")
            # set all epoch-wise metrics to be logged against epoch.
            wandb.define_metric("epoch/*", step_metric="epoch/epoch")

    def _get_lr(self) -> Union[float, None]:
        try:
            if isinstance(self.model.optimizer, keras.optimizers.Optimizer):
                return float(self.model.optimizer.learning_rate.numpy().item())
        except Exception:
            if tf_backend_available:
                if isinstance(self.model.optimizer.learning_rate, tf.Tensor):
                    return float(self.model.optimizer.learning_rate.numpy().item())
                else:
                    wandb.termerror("Unable to log learning rate.", repeat=False)
                    return None
            elif torch_backend_available:
                if isinstance(self.model.optimizer.learning_rate, torch.Tensor):
                    lr = self.model.optimizer.learning_rate.to("cpu")
                    return float(lr.numpy().item())
                else:
                    wandb.termerror("Unable to log learning rate.", repeat=False)
                    return None
            elif jax_backend_available:
                try:
                    return float(np.array(self.model.optimizer.learning_rate).item())
                except Exception as e:
                    wandb.termerror("Unable to log learning rate.", repeat=False)
                    return None

    def on_epoch_end(self, epoch: int, logs: Optional[Dict[str, Any]] = None) -> None:
        logs = dict() if logs is None else {f"epoch/{k}": v for k, v in logs.items()}

        logs["epoch/epoch"] = epoch

        lr = self._get_lr()
        if lr is not None:
            logs["epoch/learning_rate"] = lr

        wandb.log(logs)

    def on_batch_end(self, batch: int, logs: Optional[Dict[str, Any]] = None) -> None:
        self.global_step += 1
        if self.logging_batch_wise and batch % self.log_freq == 0:
            logs = {f"batch/{k}": v for k, v in logs.items()} if logs else {}
            logs["batch/batch_step"] = self.global_batch

            lr = self._get_lr()
            if lr is not None:
                logs["batch/learning_rate"] = lr

            wandb.log(logs)

            self.global_batch += self.log_freq

    def on_train_batch_end(
        self, batch: int, logs: Optional[Dict[str, Any]] = None
    ) -> None:
        self.on_batch_end(batch, logs if logs else {})
