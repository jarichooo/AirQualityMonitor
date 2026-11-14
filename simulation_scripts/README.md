# Simulation Scripts

Easy-to-run scripts for sending sample air quality readings to the Henvironment server or cleaning them up afterward.

You do **not** need to log into the server with SSH. As long as your computer is on the **same Wi-Fi or local network** as the server, you can run these right from your own laptop or desktop.

---

## What Each Script Does

| Script | Condition Sent | What You Will See on the Dashboard |
| :--- | :--- | :--- |
| `simulate_good.sh` | **Good / Normal** | Safe, comfortable coop readings |
| `simulate_mid.sh` | **Warning** | Slightly elevated temperature, gas, or dust levels |
| `simulate_bad.sh` | **Critical** | High / unsafe readings needing immediate attention |
| `simulate_random.sh` | **Random Mix** | Unpredictable readings switching across good, warning, and critical |
| `delete_simulated_data.sh` | **Cleanup** | Deletes all simulated test data (your real sensor readings stay safe) |

---

## Before You Start

1. Make sure **Python 3** is installed on your computer.
2. Make sure both your computer and the Henvironment server are connected to the **same network** (same Wi-Fi or LAN).
3. Find the **server's IP address** (for example: `192.168.1.50`).
4. Open a bash terminal in the main `AirQualityMonitor` folder:
   - **Windows:** right-click inside the `AirQualityMonitor` folder and choose **Open Git Bash here**
   - **Mac / Linux:** open **Terminal** and `cd` into `AirQualityMonitor`

> **First-time setup is automatic:** if your computer is missing the database connector package, the script sets it up automatically in the background on first run.

---

## How to Run a Simulation

Type the script name followed by the server's IP address:

```bash
./simulation_scripts/simulate_good.sh 192.168.1.50
```

Replace `simulate_good.sh` with whichever condition you want (`simulate_mid.sh`, `simulate_bad.sh`, or `simulate_random.sh`), and replace `192.168.1.50` with your server's actual IP address.

- If you omit the IP address, the script will simply ask you to type it in (just press **Enter** if you are running it directly on the server itself).
- While running, the script adds the starting readings right away and then sends one new reading every minute.
- Press **`Ctrl + C`** whenever you want to stop it.

> **Tip:** To trigger an AI forecast on the simulated data, open the dashboard in your browser and click the prediction/forecast button while or after seeding.

---

## How to Delete All Simulated Data

When you are finished testing and want to clear all simulated data from the database:

```bash
./simulation_scripts/delete_simulated_data.sh 192.168.1.50
```

- If simulated data exists, it removes all simulated readings and simulated forecasts.
- If there is no simulated data in the database, it safely does nothing and finishes cleanly.
- Real ESP32 hardware readings are never touched.

---

## Common Issues

- **"Cannot reach PostgreSQL at ...:5432"**
  - Double-check that you typed the right server IP address.
  - Check that your computer and the server are on the same Wi-Fi/network.
  - On the Ubuntu server, make sure port `5432` is allowed through the firewall (`sudo ufw allow 5432/tcp`) and the database container is running.
- **"Permission denied" on Mac or Linux**
  - Run `chmod +x simulation_scripts/*.sh` once, then try the script again.
