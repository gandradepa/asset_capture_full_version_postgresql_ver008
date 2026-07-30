# /home/developer/auth_service/auth_controller.py

from flask_login import LoginManager
from auth_model import User # Import the User model from the same directory

# Initialize the LoginManager
login_manager = LoginManager()

# Tell the LoginManager how to find a specific user.
# This is called automatically on every request for a logged-in user.
@login_manager.user_loader
def load_user(user_id):
    """Loads a user from the database given their ID."""
    return User.query.get(int(user_id))

# Set the default page to redirect to if a user needs to log in.
# We'll create the 'auth.login' route in the next phase.
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'