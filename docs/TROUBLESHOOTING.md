# Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/ready` returns 503 with `"model": false`; inspections return **503 model not available** | `models/wafer-ensemble/manifest.json` missing or unreadable | Check `WG_MODELS__CHAMPION_DIR`. In compose the folder is mounted from `./models`. See the `champion model not loaded` log line for the exact error. |
| `/ready` 503 with `"database": false` | Wrong `WG_DATABASE_URL` or Postgres not up | `docker compose logs postgres`. The URL scheme must be `postgresql+psycopg://`. |
| Batch job stays **queued** forever | Redis configured but no worker running | `docker compose ps worker`. Start with `docker compose up -d worker`, or unset `WG_REDIS_URL` to use the in-process queue. |
| **415** "unsupported or corrupt image" | File isn't a real image, or has the wrong extension | Check with `file x.png`. Raw sensor dumps need `.raw`/`.bin` plus width/height/dtype. |
| **415** "raw buffer is N bytes; ... needs M bytes" | Raw geometry doesn't match the file | Correct width, height, `raw_dtype` (uint8/uint16/float32) and channels. |
| **413** on upload | File exceeds `max_upload_mb` (default 64) | Raise `WG_MAX_UPLOAD_MB`. Behind NGINX also raise `proxy-body-size`. |
| Result shows "Continuous-tone camera image" warning | Input was a photo, not a wafer map | Expected. The shipped model is trained on wafer maps. Train on your own camera images (docs/TRAINING.md) for optical inspection. |
| Many small boxes / no boxes on the image | Localization thresholds | `find_regions(min_area_frac, rel_to_largest)` in `inference/localization.py`. Random and Near-full intentionally get one whole-wafer region. |
| Everything is `needs_review` | `severity.review_confidence` too high for your data | Lower it in the profile (e.g. 0.7). Check the model version in Administration → Models. |
| Camera: "cannot open camera source" | Wrong index, device not passed to the container, or missing GStreamer/GenICam support | `GET /api/v1/cameras/devices`. Add `devices:` in compose. GigE: `gst:` pipeline with an Aravis-enabled OpenCV, or `genicam:<producer.cti>` with `pip install harvesters`. |
| Camera effective FPS lower than target | Inference + storage slower than the frame period | Disable TTA on edge (`models.tta: false`), raise `camera_store_every`, use TensorRT/OpenVINO. |
| Browser camera tab says it cannot access cameras | Page not served over HTTPS (browsers require a secure context except on localhost) | Serve via TLS (ingress / reverse proxy), or use server-side cameras. |
| UI shows "Reconnecting" dot | WebSocket blocked by a proxy | Enable upgrade headers. NGINX: `proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";` and a long `proxy-read-timeout` (the k8s ingress has these). |
| Alerts appear in the UI but no email / Teams / SMS | Channel not configured or rejected | The alert's "Sent to" column shows `error: ...`. Use Alerts → Send test alert. Check `notifiers.*` settings and `min_level`. |
| TensorRT first start is slow | Engines are being built | Normal, one time. Cached in `trt_cache/` (`WG_TRT_CACHE`). |
| `onnxruntime` doesn't list CUDA/TensorRT | CPU wheel installed | `pip uninstall onnxruntime && pip install onnxruntime-gpu` (on Jetson: the JetPack wheel, see Dockerfile.jetson). |
| Training killed / machine rebooted | | Re-run the same command. It resumes from the last epoch checkpoint. |
| Training accuracy is much higher than the old app's | | See README "Model and measured accuracy". Only compare numbers from the leakage-safe split. |
| Forgot the admin password | | Stop the app, then run: `python -c "from waferguard.api.settings import get_settings;from waferguard.api.db import Database,User;from waferguard.api.security import hash_password;d=Database(get_settings().database_url);s=d.Session();u=s.query(User).filter_by(username='admin').one();u.password_hash=hash_password('NewPass123');s.commit()"` |
| Desktop app window doesn't open (Linux) | No GTK/Qt WebKit for pywebview | It falls back to the default browser automatically. Or install `python3-gi gir1.2-webkit2-4.1`. |
