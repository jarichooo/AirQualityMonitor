import psycopg2
from werkzeug.security import generate_password_hash

# Connect to the database
conn = psycopg2.connect(
    host="postgres_db", 
    database="air_quality", 
    user="admin", 
    password="notsosecretpass"
)
cursor = conn.cursor()

# Hash the passwords securely
admin_hash = generate_password_hash("notsosecretpass")
viewer_hash = generate_password_hash("pod2026defended")

# Insert the records
cursor.execute(
    "INSERT INTO dashboard_users (username, password_hash, role) VALUES (%s, %s, %s)", 
    ("viewer", viewer_hash, "viewer")
)
cursor.execute(
    "INSERT INTO dashboard_users (username, password_hash, role) VALUES (%s, %s, %s)", 
    ("admin", admin_hash, "admin")
)

# Save and close
conn.commit()
cursor.close()
conn.close()
print("Secure users successfully added to the database!")