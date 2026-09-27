# Sample data

* `wm811k/`: 27 real WM-811K wafer maps (3 per class) taken from the **held-out test split**, so
  the model never saw them during training. Use them for demos, or as a replay camera:
  camera source `synthetic:sample_data/wm811k`.
* `formats/`: the same kind of map in every supported input format:
  `.npy` (raw 0/1/2 die levels), 16-bit `.tif` (scratch), `.bmp`, `.jpg`, and a headerless
  `.raw` file (64 x 64, uint8: upload with width 64, height 64, pixel type uint8).
  The `formats/` maps are procedurally generated (waferguard/data/synthetic.py).
