"""
auth.py — Authentication Routes (Login / Register / Logout)
Flask Blueprint for user authentication.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from webapp.models import create_user, authenticate_user, get_user_by_id

auth_bp = Blueprint('auth', __name__)

# ─── Flask-Login User class ──────────────────────────────────────────────────

class User(UserMixin):
    def __init__(self, user_row):
        self.id = user_row['id']
        self.name = user_row['name']
        self.email = user_row['email']
        self.phone = user_row['phone']
        self.neighbour_name = user_row['neighbour_name']
        self.neighbour_phone = user_row['neighbour_phone']
        self.police_phone = user_row['police_phone']


def init_login_manager(app):
    """Initialize Flask-Login with the app."""
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        row = get_user_by_id(int(user_id))
        if row:
            return User(row)
        return None

    return login_manager


# ─── Routes ──────────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        user_row = authenticate_user(email, password)
        if user_row:
            user = User(user_row)
            login_user(user, remember=True)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.dashboard'))
        else:
            flash('Invalid email or password.', 'error')

    return render_template('login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        phone = request.form.get('phone', '').strip()
        neighbour_name = request.form.get('neighbour_name', '').strip()
        neighbour_phone = request.form.get('neighbour_phone', '').strip()
        police_phone = request.form.get('police_phone', '100').strip()

        # Validation
        if not all([name, email, password]):
            flash('Name, email, and password are required.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        elif len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
        else:
            user_id = create_user(name, email, password, phone,
                                   neighbour_name, neighbour_phone, police_phone)
            if user_id:
                flash('Account created! Please log in.', 'success')
                return redirect(url_for('auth.login'))
            else:
                flash('Email already registered.', 'error')

    return render_template('register.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
