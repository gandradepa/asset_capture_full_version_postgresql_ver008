# /home/developer/auth_service/reset_password.py
# A utility to reset a user's password from the command line.

import sys
from flask import Flask
sys.path.append('/home/developer/auth_service')
from auth_model import db, User, ensure_user_name_column

# --- Check for correct command-line arguments ---
if len(sys.argv) != 3:
    print("Usage: python reset_password.py <username> <new_password>")
    sys.exit(1)

username_to_reset = sys.argv[1]
new_password = sys.argv[2]

# --- Create a temporary app to interact with the database ---
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////home/developer/auth_service/users.db'
db.init_app(app)

with app.app_context():
    ensure_user_name_column()

    # Find the user
    user = User.query.filter_by(username=username_to_reset).first()

    if user:
        print(f"Found user '{username_to_reset}'. Resetting password...")
        # Set and hash the new password
        user.set_password(new_password)
        db.session.commit()
        print(f"Password for user '{username_to_reset}' has been successfully reset.")
    else:
        print(f"Error: User '{username_to_reset}' not found in the database.")
