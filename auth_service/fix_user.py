# /home/developer/auth_service/fix_user.py
# This script finds and fixes the 'gandrade' user entry.

import sys
from flask import Flask
sys.path.append('/home/developer/auth_service')
from auth_model import db, User, ensure_user_name_column

# Create a temporary app to interact with the database
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////home/developer/auth_service/users.db'
db.init_app(app)

with app.app_context():
    ensure_user_name_column()

    # Find the user with the incorrect password
    user_to_fix = User.query.filter_by(username='gandrade').first()

    if user_to_fix:
        print("Found user 'gandrade' with a plain text password. Deleting...")
        db.session.delete(user_to_fix)
        db.session.commit()
        print("Incorrect user entry deleted.")

    # Re-create the user correctly
    if not User.query.filter_by(username='gandrade').first():
        print("Re-creating user 'gandrade' with a properly hashed password...")
        new_user = User(
            username='gandrade',
            email='gilberto.andrade@ubc.ca'
        )
        # This correctly hashes the password 'test' before saving
        new_user.set_password('test')
        db.session.add(new_user)
        db.session.commit()
        print("User 'gandrade' has been fixed.")
    else:
        print("User 'gandrade' seems to be fixed already.")
