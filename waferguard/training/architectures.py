"""Model zoo.

* ``wafernet``          compact residual CNN designed for 64x64 wafer maps. Trains
                        from scratch on a CPU; this is what the shipped weights use.
* ``efficientnet_b4`` / ``efficientnet_b7`` / ``efficientnetv2_s`` / ``convnext_tiny`` /
  ``resnet152v2``       ImageNet transfer learning (keras.applications). Needs a GPU
                        in practice and downloads ImageNet weights on first use.
* ``vit_small``         Vision Transformer built from Keras layers (no extra deps),
                        suitable for from-scratch or fine-tuning on larger datasets.

Keras does not ship ResNeXt-101; ConvNeXt is the closest modern equivalent and is
offered instead.
"""
from __future__ import annotations

import keras
from keras import layers


def _res_block(x, filters, stride, l2):
    reg = keras.regularizers.l2(l2)
    shortcut = x
    y = layers.Conv2D(filters, 3, stride, padding="same", use_bias=False, kernel_regularizer=reg)(x)
    y = layers.BatchNormalization()(y)
    y = layers.Activation("swish")(y)
    y = layers.Conv2D(filters, 3, padding="same", use_bias=False, kernel_regularizer=reg)(y)
    y = layers.BatchNormalization()(y)
    # squeeze-and-excitation
    se = layers.GlobalAveragePooling2D()(y)
    se = layers.Dense(max(8, filters // 8), activation="swish")(se)
    se = layers.Dense(filters, activation="sigmoid")(se)
    y = layers.Multiply()([y, layers.Reshape((1, 1, filters))(se)])
    if stride != 1 or shortcut.shape[-1] != filters:
        shortcut = layers.Conv2D(filters, 1, stride, use_bias=False, kernel_regularizer=reg)(shortcut)
        shortcut = layers.BatchNormalization()(shortcut)
    return layers.Activation("swish")(layers.Add()([y, shortcut]))


def wafernet(input_shape, n_classes, width=32, dropout=0.3, l2=1e-4, stem_stride=2, blocks=(1, 2, 2, 1)):
    inp = keras.Input(input_shape, name="input")
    x = layers.Conv2D(width, 3, stem_stride, padding="same", use_bias=False)(inp)
    x = layers.BatchNormalization()(x)
    x = layers.Activation("swish")(x)
    for i, (mult, n) in enumerate(zip([1, 2, 4, 8], blocks)):
        x = _res_block(x, width * mult, 1 if i == 0 else 2, l2)
        for _ in range(n - 1):
            x = _res_block(x, width * mult, 1, l2)
    x = layers.Concatenate()([layers.GlobalAveragePooling2D()(x), layers.GlobalMaxPooling2D()(x)])
    x = layers.Dropout(dropout)(x)
    out = layers.Dense(n_classes, activation="softmax", name="probs")(x)
    return keras.Model(inp, out, name=f"wafernet_w{width}")


def _transfer(app_fn, preprocess, size, input_shape, n_classes, dropout, weights):
    inp = keras.Input(input_shape, name="input")
    x = layers.Resizing(size, size, interpolation="bilinear")(inp)
    x = layers.Rescaling(255.0)(x)
    x = preprocess(x)
    base = app_fn(include_top=False, weights=weights, input_shape=(size, size, 3), pooling="avg")
    x = base(x)
    x = layers.Dropout(dropout)(x)
    out = layers.Dense(n_classes, activation="softmax", name="probs")(x)
    return keras.Model(inp, out, name=base.name)


class PatchEmbed(layers.Layer):
    def __init__(self, patch, dim, n_patches, **kw):
        super().__init__(**kw)
        self.patch, self.dim, self.n_patches = patch, dim, n_patches
        self.proj = layers.Conv2D(dim, patch, patch)
        self.reshape = layers.Reshape((n_patches, dim))

    def build(self, input_shape):
        self.cls = self.add_weight(shape=(1, 1, self.dim), initializer="zeros", name="cls")
        self.pos = self.add_weight(shape=(1, self.n_patches + 1, self.dim),
                                   initializer=keras.initializers.RandomNormal(stddev=0.02), name="pos")

    def call(self, x):
        x = self.reshape(self.proj(x))
        cls = keras.ops.broadcast_to(self.cls, (keras.ops.shape(x)[0], 1, self.dim))
        return keras.ops.concatenate([cls, x], axis=1) + self.pos

    def get_config(self):
        return {**super().get_config(), "patch": self.patch, "dim": self.dim, "n_patches": self.n_patches}


def vit_small(input_shape, n_classes, patch=8, dim=192, depth=6, heads=3, dropout=0.1):
    inp = keras.Input(input_shape, name="input")
    n = (input_shape[0] // patch) * (input_shape[1] // patch)
    x = PatchEmbed(patch, dim, n)(inp)
    for _ in range(depth):
        h = layers.LayerNormalization(epsilon=1e-6)(x)
        h = layers.MultiHeadAttention(heads, dim // heads, dropout=dropout)(h, h)
        x = layers.Add()([x, h])
        h = layers.LayerNormalization(epsilon=1e-6)(x)
        h = layers.Dense(dim * 4, activation="gelu")(h)
        h = layers.Dropout(dropout)(h)
        h = layers.Dense(dim)(h)
        x = layers.Add()([x, h])
    x = layers.LayerNormalization(epsilon=1e-6)(x)
    out = layers.Dense(n_classes, activation="softmax", name="probs")(x[:, 0])
    return keras.Model(inp, out, name="vit_small")


def build(name: str, input_shape, n_classes, **kw) -> keras.Model:
    apps = keras.applications
    weights = kw.pop("weights", "imagenet")
    dropout = kw.pop("dropout", 0.3)
    if name == "wafernet":
        return wafernet(input_shape, n_classes, dropout=dropout, **kw)
    if name == "vit_small":
        return vit_small(input_shape, n_classes, **kw)
    table = {
        "efficientnet_b4": (apps.EfficientNetB4, apps.efficientnet.preprocess_input, 380),
        "efficientnet_b7": (apps.EfficientNetB7, apps.efficientnet.preprocess_input, 600),
        "efficientnetv2_s": (apps.EfficientNetV2S, apps.efficientnet_v2.preprocess_input, 384),
        "convnext_tiny": (apps.ConvNeXtTiny, apps.convnext.preprocess_input, 224),
        "resnet152v2": (apps.ResNet152V2, apps.resnet_v2.preprocess_input, 224),
    }
    if name not in table:
        raise ValueError(f"unknown architecture {name!r}; choose from wafernet, vit_small, {', '.join(table)}")
    fn, pre, size = table[name]
    size = kw.pop("image_size", size)
    return _transfer(fn, pre, size, input_shape, n_classes, dropout, weights)
