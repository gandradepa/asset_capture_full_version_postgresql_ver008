# /home/developer/asset_capture_app_dev/auth_routes.py

import re
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user

# Note: We import from the shared auth_service directory
from auth_model import User, db, bcrypt

# Create a Blueprint for auth routes
auth_bp = Blueprint('auth', __name__, template_folder='templates')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('start')) # Redirect to the app's main page

    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.check_password(request.form.get('password')):
            login_user(user, remember=request.form.get('remember'))
            next_page = request.args.get('next')
            return redirect(next_page or url_for('start'))
        else:
            flash('Invalid username or password.', 'danger')
            
    # MODIFIED: Set username to None since no user is logged in here
    return render_template('login.html', username=None)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))

@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not current_user.check_password(old_password):
            flash('Your old password was entered incorrectly. Please try again.', 'danger')
            return redirect(url_for('auth.change_password'))

        if new_password != confirm_password:
            flash('New passwords do not match.', 'danger')
            return redirect(url_for('auth.change_password'))

        if len(new_password) < 8 or not re.search(r'[!@#$%^&*(),.?":{}|<>]', new_password):
            flash('Password must be at least 8 characters and include one special character.', 'danger')
            return redirect(url_for('auth.change_password'))

        current_user.set_password(new_password)
        db.session.commit()

        flash('Your password has been updated successfully!', 'success')
        return redirect(url_for('start')) # Redirect to the app's main page
        
    # MODIFIED: Get username only if the user is authenticated
    username = current_user.username if current_user.is_authenticated else None
    return render_template('change_password.html', username=username)