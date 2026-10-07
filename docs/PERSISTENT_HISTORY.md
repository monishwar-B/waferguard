# Keep history, users and images (hosted deployments)

**Symptom:** after a restart or redeploy the History page is empty, users you created are gone, or old
inspections show no image.

**Cause:** free hosts such as Render erase the server's disk whenever the service restarts or redeploys.
WaferGuard's default database is a SQLite file on that disk, and images are saved next to it.

**Fix:** use an external PostgreSQL database. WaferGuard then keeps the records *and the images* in it
(`WG_IMAGE_STORE=auto` picks this automatically for PostgreSQL), so nothing depends on the server's disk.

## 1. Create a free database (Neon, no card needed)
1. Sign up at neon.tech and create a project.
2. Copy the **connection string**. It looks like
   `postgresql://user:password@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require`.

## 2. Give it to the API
On Render: **waferguard-api -> Environment -> Add Environment Variable**

| Key | Value |
|---|---|
| `WG_DATABASE_URL` | the connection string from step 1 (`postgres://` and `postgresql://` both work) |
| `WG_BOOTSTRAP_ADMIN_PASSWORD` | the password you want for the `admin` account |

Save. Render redeploys. The first start creates all tables and the `admin` user in the new database.

## 3. Check it
Open `https://<your-service>.onrender.com/ready`. You should see:

```json
{"ready": true, "database_kind": "postgresql", "image_store": "db", ...}
```

If it says `"database_kind": "sqlite"`, the variable was not picked up, and history will still be lost on restart.

## Notes
- `WG_BOOTSTRAP_ADMIN_PASSWORD` is only read when the database has **no users yet**. To change the password
  later, use Administration -> Users, or delete the admin row and restart.
- Images are stored inside the database, roughly 20-60 KB per inspection. The Neon free plan (0.5 GB) holds
  many thousands. Set `WG_IMAGE_STORE=disk` to keep files on a disk volume instead.
- Existing records keep working if you switch modes: a `db:` reference is read from the database, anything else from disk.
- Records created while the old SQLite file was in use cannot be moved automatically; start fresh on PostgreSQL.
