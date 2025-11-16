# Quick Deployment Guide - Current Airport Status Feature

## Step 1: Rebuild the API Container

```bash
cd /mnt/c/Users/llew/Documents/github\ local/runway_detection_system
docker-compose up -d --build api
```

Expected output:
```
Building api
[+] Building X.Xs
Successfully built
Successfully tagged runway_detection_system_api:latest
Recreating runway_api ... done
```

## Step 2: Verify Container is Running

```bash
docker-compose ps
```

Look for `runway_api` with status `Up` and `healthy`.

## Step 3: Check Logs for Errors

```bash
docker logs runway_api --tail 50
```

Should see:
```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

## Step 4: Test the New API Endpoint

```bash
curl http://localhost:8000/api/dashboard/current-airports | jq '.[0]'
```

Expected response (example):
```json
{
  "airport_code": "KATL",
  "arriving": ["26L", "27R"],
  "departing": ["26L", "27R"],
  "flow": "WEST",
  "last_change": "2025-11-16T14:30:00.123456",
  "recent_changes": [
    {
      "time": "2025-11-16T14:30:00.123456",
      "from_arriving": ["8R", "9L"],
      "from_departing": ["8R", "9L"],
      "to_arriving": ["26L", "27R"],
      "to_departing": ["26L", "27R"],
      "duration_minutes": 120
    }
  ]
}
```

## Step 5: Access the Dashboard

1. Open browser: http://localhost:8000/dashboard
2. **Hard refresh** to clear cache: `Ctrl+Shift+R` (or `Cmd+Shift+R` on Mac)
3. Look for new section: "✈️ Current Airport Status"

## Step 6: Test the Features

### Pin an Airport
- Click the 📌 icon next to any airport
- It should change to 📍 and move to the top
- Refresh the page - pin should persist

### Expand History
- Click on any airport row (not the pin icon)
- Drawer should expand showing recent changes
- Click again to collapse

### Multiple Drawers
- Expand multiple airports
- All should stay open
- Auto-refresh should preserve open state

## Troubleshooting

### Issue: Container won't start
```bash
docker logs runway_api
```
Look for Python syntax errors or import errors.

### Issue: API endpoint returns 500 error
```bash
docker logs runway_api --tail 100
```
Check for database connection errors or SQL syntax issues.

### Issue: Dashboard doesn't update
1. Hard refresh: `Ctrl+Shift+R`
2. Open browser console (F12) and check for JavaScript errors
3. Verify API endpoint works: `curl http://localhost:8000/api/dashboard/current-airports`

### Issue: Pins don't persist
- Check browser cookies are enabled
- Look for cookie named `pinned_airports` in browser dev tools

### Issue: No airports shown
- Check database has data: 
  ```bash
  docker exec runway_db psql -U postgres -d runway_detection -c "SELECT COUNT(*) FROM runway_configs"
  ```
- Verify collector is running:
  ```bash
  docker logs runway_collector --tail 50
  ```

## Rollback (if needed)

```bash
# Restore backup
cp runway_api.py.backup runway_api.py

# Rebuild
docker-compose up -d --build api
```

---

For detailed implementation information, see IMPLEMENTATION_SUMMARY.md
