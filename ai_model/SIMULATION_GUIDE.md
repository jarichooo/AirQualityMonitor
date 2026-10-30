# Run the farm simulation

Use this guide after the server has been set up and updated.

## 1. Start

Connect to the server through SSH. Stop any previous simulation with **Ctrl+C**
and wait for it to finish. Keep physical sensor collection paused during the demo.

Paste this whole block into the server terminal:

```bash
(
  set -e
  cd ~/AirQualityMonitor
  sudo docker compose --profile forecast stop ingestion forecast_worker
  sudo docker compose up -d --wait postgres_db
  sudo docker compose up -d --no-deps web_dashboard
  sudo systemctl start henvironment-ollama.service
  curl --retry 15 --retry-connrefused --retry-delay 2 -fsS \
    http://127.0.0.1:11435/api/tags >/dev/null

  read -rsp 'PostgreSQL password: ' DB_PASS; echo
  export DB_PASS
  sudo --preserve-env=DB_PASS docker compose --profile forecast run \
    --rm --no-deps --name farm_simulation forecast_worker --simulate series
)
```

Enter the passwords when prompted. Typed passwords are hidden.
Keep this terminal open. Qwen runs in the background; you only need one terminal.

The simulation runs **good → warning → critical**, then finishes automatically.
Each stage has five updates. Allow about **15 minutes**, or longer if Qwen is slow.

**Starting a run removes earlier simulation readings and forecasts.**
The original dataset, real sensor readings, and user accounts stay intact.

## 2. View the results

Open `http://<server-IP>:5000/dashboard` and sign in.

- **Predicted values in 2 hours** shows the model's numbers first.
- **Reasoning AI guidance** below shows Qwen's explanation. Its first response
  can take around two minutes; it appears automatically when ready.
- Green means Normal, yellow means Warning, and red means Critical.
- Click **Refresh** to update the measured readings and charts.

You do not need to wait two hours. The model predicts two hours ahead now.

## 3. Stop or run again

Press **Ctrl+C** and wait for the command to exit. Leave Qwen and the dashboard running.

To run again, paste the same startup block. Do not run two simulations at once.
Results from the current run remain stored until the next run starts.

If a command fails or Qwen reports an error, save the terminal message and ask
the person maintaining the server. Technical setup and cleanup instructions are
in [SERVER_COMMANDS.md](../SERVER_COMMANDS.md).
