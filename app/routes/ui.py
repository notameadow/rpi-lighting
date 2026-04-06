import os
from flask import Blueprint, render_template, make_response, request, redirect, url_for, session, send_file
from app.auth import require_auth, check_credentials, _is_locked_out, _record_failure, _record_success

ui_bp = Blueprint('ui', __name__)


@ui_bp.route('/')
@require_auth
def controller():
    resp = make_response(render_template('controller.html'))
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@ui_bp.route('/logo')
def logo():
    custom = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'data', 'logo.png')
    if os.path.exists(custom):
        return send_file(custom, mimetype='image/png')
    default = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'icon-192.png')
    return send_file(default, mimetype='image/png')


@ui_bp.route('/sw.js')
def service_worker():
    sw_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'sw.js')
    resp = make_response(send_file(sw_path, mimetype='application/javascript'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp


@ui_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    ip = request.remote_addr

    if _is_locked_out(ip):
        error = 'Too many failed attempts. Try again later.'
        return render_template('login.html', error=error), 429

    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if check_credentials(username, password):
            _record_success(ip)
            session['authenticated'] = True
            session.permanent = True
            return redirect(url_for('ui.controller'))
        else:
            _record_failure(ip)
            error = 'Invalid credentials.'

    return render_template('login.html', error=error)


@ui_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('ui.login'))
