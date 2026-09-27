from flask import Flask, render_template, request, redirect, url_for, session, flash
import psycopg2
import os

app = Flask(__name__)
app.secret_key = "defense_pod_2026_secure_key" 

DB_HOST = "postgres_db"
DB_NAME = "air_quality"
DB_USER = "admin"
DB_PASS = "notsosecretpass" 

# authentication 
USERS = {
    "admin1": "notsosecretpass1",
    "admin2": "notsosecretpass2"
}

def get_db_connection():
    return psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if username in USERS and USERS[username] == password:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid credentials. Please try again.", "danger")
            
    return render_template('login.html')

@app.route('/')
def dashboard():
    # Route Protection: Kick unauthenticated users back to login
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    # Fetch the latest 20 rows of data
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT recorded_at, temperature_c, humidity_perc, nh3_ppm, pm25_ugm3, mq135_ppm, mq137_ppm 
        FROM air_quality_logs 
        ORDER BY recorded_at DESC LIMIT 20;
    """)
    readings = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return render_template('dashboard.html', readings=readings, username=session['username'])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)