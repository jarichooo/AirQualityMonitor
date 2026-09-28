from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.security import check_password_hash
import psycopg2
import psycopg2.extras
import os

app = Flask(__name__)
app.secret_key = "defense_pod_2026_secure_key" 

DB_HOST = "postgres_db"
DB_NAME = "air_quality"
DB_USER = "admin"
DB_PASS = "notsosecretpass" 


def get_db_connection():
    return psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        try:
            # connect to postgres database container
            conn = psycopg2.connect(
                host="postgres_db", 
                database="air_quality", 
                user="admin", 
                password="notsosecretpass"
            )
            # use DictCursor to reference columns by name easily
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            
            # look up the user by username
            cursor.execute("SELECT password_hash, role FROM dashboard_users WHERE username = %s", (username,))
            user = cursor.fetchone()

            # verify the hash matches the entered password
            if user and check_password_hash(user['password_hash'], password):
                session['logged_in'] = True
                session['username'] = username
                session['role'] = user['role']
                return redirect(url_for('dashboard'))
            else:
                error = "Invalid username or password."

        except Exception as e:
            error = f"Database error: {e}"
        finally:
            # clean up the database connection
            if 'cursor' in locals(): cursor.close()
            if 'conn' in locals(): conn.close()

    return render_template('login.html', error=error)

@app.route('/')
def dashboard():
    # route Protection: Kick unauthenticated users back to login
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Fetch total record count
    cursor.execute("SELECT COUNT(*) FROM air_quality_logs;")
    total_rows = cursor.fetchone()[0]
    
    # 2. Fetch the latest 20 rows of data for the table
    cursor.execute("""
        SELECT recorded_at, temperature_c, humidity_perc, nh3_ppm, pm25_ugm3, mq135_ppm, mq137_ppm 
        FROM air_quality_logs 
        ORDER BY recorded_at DESC LIMIT 20;
    """)
    readings = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    # Pass both readings and total_rows to the template
    return render_template('dashboard.html', readings=readings, total_rows=total_rows, username=session['username'])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)