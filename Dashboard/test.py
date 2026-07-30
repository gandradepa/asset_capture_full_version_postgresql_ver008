from flask import Flask, render_template
import sys
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SERVER_NAME'] = 'localhost'
ctx = app.app_context()
ctx.push()
try:
    print("Trying dashboard...")
    res = render_template('dashboard.html', is_admin=True)
    print("dashboard.html OK!")
except Exception as e:
    import traceback
    traceback.print_exc()
