# Testing Guide (`simulation_scripts`)

This folder contains ready-to-use test scripts for **people testing the Henvironment Air Quality Dashboard** without needing physical sensors or server login access.

As long as your laptop or computer is connected to the **same Wi-Fi or local network** as the Henvironment server, you can run these scripts straight from your own device.

---

## Which Script Should I Run?

Pick the test scenario you want to see on the dashboard:

| Script | Air Quality Scenario | What It Simulates |
| :--- | :--- | :--- |
| `simulate_good.sh` | **Good / Normal** | Healthy, normal coop conditions |
| `simulate_mid.sh` | **Warning** | Slightly high temperature, gas, or dust levels |
| `simulate_bad.sh` | **Critical** | Unsafe levels that trigger alerts and warnings |
| `simulate_random.sh` | **Random Mix** | Mixed readings across good, warning, and critical levels |
| `delete_simulated_data.sh` | **Clean Up** | Removes all test data created by these scripts (safe for real sensor data) |

---

## What You Need Before Starting

1. **Same network:** Connect your computer to the same Wi-Fi or LAN as the Henvironment server.
2. **Server IP address:** Ask the project team for the server's local IP address (it looks something like `192.168.1.50`).
3. **Python 3 installed** on your computer.
4. A bash terminal:
   - **Windows:** Open **Git Bash** inside the `AirQualityMonitor` folder (right-click the folder → **Open Git Bash here**).
   - **Mac / Linux:** Open **Terminal** and go into the `AirQualityMonitor` folder (`cd path/to/AirQualityMonitor`).

*(The first time you run a script, it may pause for a few seconds to install a small helper package automatically — you don't need to install anything manually.)*

---

## Step 1: Run a Test Scenario

Run the script you want with the server's IP address at the end:

```bash
./simulation_scripts/simulate_good.sh 192.168.1.50
```

*(Replace `192.168.1.50` with the actual IP address of the server, and swap `simulate_good.sh` for `simulate_mid.sh`, `simulate_bad.sh`, or `simulate_random.sh` as needed.)*

If you forget to include the IP address, the script will ask you to type it in.

### What happens while it runs
- Immediately sends starter sample readings so the dashboard cards and charts update right away.
- Continues sending 1 new sample reading every minute while open.
- Open the **Henvironment web dashboard** in your browser to inspect the cards, charts, and 2-hour forecast.
- Press **`Ctrl + C`** in the terminal whenever you want to stop the test.

---

## Step 2: Clean Up When You're Done

After finishing your tests, run the cleanup script so no sample test data stays mixed into the system:

```bash
./simulation_scripts/delete_simulated_data.sh 192.168.1.50
```

- Deletes any simulated readings and simulated forecasts found on the server.
- Safe to run even if there is no test data yet.
- Does **not** delete real sensor readings from actual hardware.

---

## If Something Doesn't Work

- **Script says it cannot reach the server:**
  - Make sure your device is on the same Wi-Fi/network as the server.
  - Double-check the server IP address with the project team.
  - Let the server host know if connection to port `5432` is blocked on the server.
- **"Permission denied" on Mac or Linux:**
  - Run `chmod +x simulation_scripts/*.sh` once, then re-run your script.
- **"Python 3 is required" message:**
  - Install Python 3 from [python.org](https://www.python.org/downloads/), then open a fresh terminal window and try again.
