import os
import hmac
import secrets
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, abort
from database import init_db, get_db_connection

app = Flask(__name__)

IS_PRODUCTION = bool(os.environ.get('DATABASE_URL'))

# Gizli anahtar ortam değişkeninden gelir; yoksa yerelde geçici bir anahtar üretilir
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
if not os.environ.get('SECRET_KEY'):
    print("UYARI: SECRET_KEY tanımlı değil, geçici anahtar kullanılıyor (sunucu yeniden başlayınca oturumlar düşer).")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
)

# Yönetici şifresi: canlıda mutlaka ADMIN_PASSWORD ile verilmeli
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or (None if IS_PRODUCTION else 'mert2026')

LESSON_STATUSES = ('planlandi', 'yapildi', 'iptal')
MONTHS_SHORT = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara']
MONTHS_LONG = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']
DAYS_LONG = ['Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar']

# Tabloları başlat
init_db()


# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------
def _parse_dt(value):
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace(' ', 'T')
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


@app.template_filter('tl')
def format_tl(value):
    """1234.5 -> '1.234,50 ₺' , 1500 -> '1.500 ₺' (Türkçe ayraçlar)."""
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount.is_integer():
        text = f"{amount:,.0f}"
    else:
        text = f"{amount:,.2f}"
    text = text.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f"{text} ₺"


@app.template_filter('tarih')
def format_tarih(value, with_time=True):
    dt = _parse_dt(value)
    if not dt:
        return value or ''
    text = f"{dt.day} {MONTHS_SHORT[dt.month - 1]} {dt.year}"
    if with_time and (dt.hour or dt.minute):
        text += f", {dt:%H:%M}"
    return text


@app.template_filter('saat')
def format_saat(value):
    dt = _parse_dt(value)
    return f"{dt:%H:%M}" if dt else ''


def is_admin():
    return session.get('user_role') == 'admin'


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Bu işlem için yönetici girişi gerekli.'}), 403
            flash('Bu işlem için yönetici girişi gerekli.', 'danger')
            return redirect(request.referrer or url_for('index'))
        return view(*args, **kwargs)
    return wrapped


def get_csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return token


# --------------------------------------------------------------------------
# İstek öncesi kontroller
# --------------------------------------------------------------------------
@app.before_request
def require_login():
    # Login sayfası ve static dosyalar hariç diğer tüm sayfalarda oturum kontrolü yap
    allowed_routes = ['login', 'static']
    if request.endpoint and request.endpoint not in allowed_routes:
        if not session.get('user_role'):
            return redirect(url_for('login'))


@app.before_request
def csrf_protect():
    if request.method == 'POST':
        sent = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        expected = session.get('csrf_token')
        if not sent or not expected or not hmac.compare_digest(sent, expected):
            abort(400)


@app.context_processor
def inject_globals():
    return dict(is_admin=is_admin(), csrf_token=get_csrf_token)


# --------------------------------------------------------------------------
# Giriş / Çıkış
# --------------------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'visitor':
            session.clear()
            session['user_role'] = 'visitor'
            return redirect(url_for('index'))

        password = request.form.get('password', '')
        if not ADMIN_PASSWORD:
            flash('Yönetici şifresi sunucuda tanımlanmamış (ADMIN_PASSWORD).', 'danger')
        elif hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
            session.clear()
            session['user_role'] = 'admin'
            return redirect(url_for('index'))
        else:
            flash('Şifre hatalı.', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# --------------------------------------------------------------------------
# Genel Bakış
# --------------------------------------------------------------------------
@app.route('/')
def index():
    conn = get_db_connection()
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')

    # Bugünkü dersler
    todays_lessons = conn.execute('''
        SELECT l.id, l.lesson_date, l.duration_minutes, l.status, l.topic, s.id as student_id, s.full_name, s.hourly_rate
        FROM lessons l
        JOIN students s ON l.student_id = s.id
        WHERE substr(CAST(l.lesson_date AS text), 1, 10) = %s
        ORDER BY l.lesson_date ASC
    ''', (today_str,)).fetchall()

    # Bekleyen ödemeler toplamı (status = 'yapildi' ve ödenmemiş)
    pending_row = conn.execute('''
        SELECT SUM(s.hourly_rate) as total_debt, COUNT(l.id) as lesson_count
        FROM lessons l
        JOIN students s ON l.student_id = s.id
        WHERE l.status = 'yapildi'
        AND l.id NOT IN (SELECT lesson_id FROM payments WHERE is_paid = 1)
    ''').fetchone()
    total_pending = pending_row['total_debt'] if pending_row and pending_row['total_debt'] else 0
    pending_lesson_count = pending_row['lesson_count'] if pending_row and pending_row['lesson_count'] else 0

    # Son 6 ayın gelir istatistiği
    chart_labels = []
    chart_data = []
    for i in range(5, -1, -1):
        m = now.month - i
        y = now.year
        if m <= 0:
            m += 12
            y -= 1
        month_str = f"{y}-{m:02d}"

        income_row = conn.execute('''
            SELECT SUM(amount) as m_income
            FROM payments
            WHERE is_paid = 1 AND substr(CAST(payment_date AS text), 1, 7) = %s
        ''', (month_str,)).fetchone()

        income = income_row['m_income'] if income_row and income_row['m_income'] else 0
        chart_labels.append(f"{MONTHS_SHORT[m - 1]} {y}")
        chart_data.append(float(income))

    this_month_income = chart_data[-1]
    start_m, start_y = (now.month - 5, now.year) if now.month > 5 else (now.month + 7, now.year - 1)
    start_text = MONTHS_LONG[start_m - 1] + ('' if start_y == now.year else f' {start_y}')
    chart_range = f"{start_text} – {MONTHS_LONG[now.month - 1]} {now.year}"

    # Ziyaretçi ise finansal veriler gönderilmez
    if not is_admin():
        chart_data = [0] * len(chart_data)
        total_pending = 0
        this_month_income = 0

    # Hızlı notlar
    quick_notes = conn.execute('SELECT * FROM quick_notes ORDER BY created_at DESC').fetchall()

    student_count_row = conn.execute('SELECT COUNT(*) as c FROM students').fetchone()
    student_count = student_count_row['c'] if student_count_row else 0

    conn.close()

    today_label = f"{now.day} {MONTHS_LONG[now.month - 1]} {now.year}, {DAYS_LONG[now.weekday()]}"
    return render_template('index.html',
                           todays_lessons=todays_lessons,
                           total_pending=total_pending,
                           pending_lesson_count=pending_lesson_count,
                           this_month_income=this_month_income,
                           this_month_label=f"{MONTHS_LONG[now.month - 1]} {now.year}",
                           chart_labels=chart_labels,
                           chart_data=chart_data,
                           chart_range=chart_range,
                           quick_notes=quick_notes,
                           student_count=student_count,
                           today_label=today_label)


# --------------------------------------------------------------------------
# Öğrenciler
# --------------------------------------------------------------------------
@app.route('/students')
def students():
    conn = get_db_connection()
    students_data = conn.execute('''
        SELECT s.*,
            (SELECT COUNT(*) FROM lessons l WHERE l.student_id = s.id AND l.status = 'yapildi'
                AND l.id NOT IN (SELECT lesson_id FROM payments WHERE is_paid = 1)) as unpaid_count
        FROM students s
        ORDER BY s.full_name ASC
    ''').fetchall()
    conn.close()
    return render_template('students.html', students=students_data)


@app.route('/student/<int:id>')
def student_profile(id):
    conn = get_db_connection()
    student = conn.execute('SELECT * FROM students WHERE id = %s', (id,)).fetchone()

    if not student:
        flash('Öğrenci bulunamadı.', 'danger')
        conn.close()
        return redirect(url_for('students'))

    lessons = conn.execute('''
        SELECT id, lesson_date, duration_minutes, status, topic, homework, notes
        FROM lessons
        WHERE student_id = %s
        ORDER BY lesson_date DESC
    ''', (id,)).fetchall()

    stats = conn.execute('''
        SELECT
            COUNT(id) as total_lessons,
            SUM(CASE WHEN status = 'yapildi' THEN 1 ELSE 0 END) as done_lessons
        FROM lessons WHERE student_id = %s
    ''', (id,)).fetchone()

    unpaid_count_row = conn.execute('''
        SELECT COUNT(id) as c FROM lessons
        WHERE student_id = %s AND status = 'yapildi'
        AND id NOT IN (SELECT lesson_id FROM payments WHERE is_paid = 1)
    ''', (id,)).fetchone()
    unpaid_count = unpaid_count_row['c'] if unpaid_count_row else 0

    pending_balance = unpaid_count * float(student['hourly_rate'])
    conn.close()

    # WhatsApp mesajı yalnızca yöneticiye hazırlanır (bakiye bilgisi içerir)
    wa_link = None
    if is_admin():
        greeting = 'Merhaba'
        amount_text = format_tl(pending_balance)
        if pending_balance > 0:
            wa_text = f"{greeting}, {student['full_name']} için tamamlanan {unpaid_count} dersin toplam bakiyesi {amount_text}. Bilginize sunarım, iyi günler dilerim."
        else:
            wa_text = f"{greeting}, {student['full_name']} ile dersimizi tamamladık. Ödemeler günceldir. İyi günler dilerim."

        phone_clean = ''.join(c for c in (student['parent_contact'] or '') if c.isdigit())
        if phone_clean.startswith('0'):
            phone_clean = '90' + phone_clean[1:]
        elif len(phone_clean) == 10:
            phone_clean = '90' + phone_clean

        if phone_clean:
            wa_link = f"https://wa.me/{phone_clean}?text={urllib.parse.quote(wa_text)}"
        else:
            wa_link = f"https://wa.me/?text={urllib.parse.quote(wa_text)}"

    return render_template('student_profile.html',
                           student=student,
                           lessons=lessons,
                           stats=stats,
                           unpaid_count=unpaid_count,
                           pending_balance=pending_balance,
                           wa_link=wa_link)


def _student_form():
    """Formdan öğrenci alanlarını okur; hata varsa (None, mesaj) döner."""
    full_name = request.form.get('full_name', '').strip()
    grade_level = request.form.get('grade_level', '').strip()
    parent_contact = request.form.get('parent_contact', '').strip()
    notes = request.form.get('notes', '').strip()
    if not full_name:
        return None, 'Ad soyad boş bırakılamaz.'
    try:
        hourly_rate = float(request.form.get('hourly_rate', '').replace(',', '.'))
        default_duration = int(request.form.get('default_duration') or 60)
    except ValueError:
        return None, 'Ders ücreti ve süre sayı olmalı.'
    if hourly_rate < 0 or not (15 <= default_duration <= 480):
        return None, 'Ücret negatif olamaz, süre 15–480 dakika arasında olmalı.'
    return (full_name, grade_level, parent_contact, hourly_rate, default_duration, notes), None


@app.route('/students/add', methods=['POST'])
@admin_required
def add_student():
    values, error = _student_form()
    if error:
        flash(error, 'danger')
    else:
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO students (full_name, grade_level, parent_contact, hourly_rate, default_duration, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', values)
        conn.commit()
        conn.close()
        flash(f'{values[0]} kaydedildi.', 'success')

    return redirect(url_for('students'))


@app.route('/students/edit/<int:id>', methods=['POST'])
@admin_required
def edit_student(id):
    values, error = _student_form()
    if error:
        flash(error, 'danger')
    else:
        conn = get_db_connection()
        conn.execute('''
            UPDATE students
            SET full_name = %s, grade_level = %s, parent_contact = %s, hourly_rate = %s, default_duration = %s, notes = %s
            WHERE id = %s
        ''', (*values, id))
        conn.commit()
        conn.close()
        flash('Öğrenci bilgileri güncellendi.', 'success')

    return redirect(request.referrer or url_for('students'))


@app.route('/students/delete/<int:id>', methods=['POST'])
@admin_required
def delete_student(id):
    conn = get_db_connection()
    # İlişkili dersleri ve ödemeleri temizle
    lessons = conn.execute('SELECT id FROM lessons WHERE student_id = %s', (id,)).fetchall()
    for l in lessons:
        conn.execute('DELETE FROM payments WHERE lesson_id = %s', (l['id'],))
    conn.execute('DELETE FROM lessons WHERE student_id = %s', (id,))
    conn.execute('DELETE FROM students WHERE id = %s', (id,))
    conn.commit()
    conn.close()
    flash('Öğrenci ve ilişkili tüm kayıtlar silindi.', 'warning')
    return redirect(url_for('students'))


# --------------------------------------------------------------------------
# Takvim
# --------------------------------------------------------------------------
@app.route('/schedule')
def schedule():
    conn = get_db_connection()
    students_list = conn.execute('SELECT * FROM students ORDER BY full_name ASC').fetchall()
    conn.close()
    return render_template('schedule.html', students=students_list)


@app.route('/api/lessons')
def api_lessons():
    conn = get_db_connection()
    lessons = conn.execute('''
        SELECT l.id, l.lesson_date, l.duration_minutes, l.status, l.topic, l.homework, l.notes, s.full_name
        FROM lessons l
        JOIN students s ON l.student_id = s.id
    ''').fetchall()
    conn.close()

    events = []
    for row in lessons:
        start_dt = _parse_dt(row['lesson_date'])
        if not start_dt:
            continue
        duration = row['duration_minutes'] or 60
        end_dt = start_dt + timedelta(minutes=duration)
        status = row['status'] if row['status'] in LESSON_STATUSES else 'planlandi'

        events.append({
            'id': row['id'],
            'title': row['full_name'],
            'start': start_dt.isoformat(),
            'end': end_dt.isoformat(),
            # Renk, temaya göre CSS'teki .ev-<durum> sınıfından gelir
            'classNames': [f'ev-{status}'],
            'extendedProps': {
                'status': status,
                'studentName': row['full_name'],
                'duration': duration,
                'topic': row['topic'] or '',
                'homework': row['homework'] or '',
                'notes': row['notes'] or ''
            }
        })

    return jsonify(events)


@app.route('/schedule/add', methods=['POST'])
@admin_required
def add_lesson():
    student_id = request.form.get('student_id')
    lesson_date = request.form.get('lesson_date')  # YYYY-MM-DDTHH:MM
    try:
        weeks = max(1, min(int(request.form.get('weeks', 1)), 52))
    except ValueError:
        weeks = 1

    if not student_id or not lesson_date:
        flash('Öğrenci ve tarih seçimi zorunlu.', 'danger')
        return redirect(url_for('schedule'))

    try:
        start_dt = datetime.strptime(lesson_date, '%Y-%m-%dT%H:%M')
    except ValueError:
        flash('Tarih biçimi geçersiz.', 'danger')
        return redirect(url_for('schedule'))

    conn = get_db_connection()
    student = conn.execute('SELECT default_duration FROM students WHERE id = %s', (student_id,)).fetchone()
    if not student:
        conn.close()
        flash('Öğrenci bulunamadı.', 'danger')
        return redirect(url_for('schedule'))
    duration = student['default_duration'] or 60

    existing_lessons = conn.execute("SELECT lesson_date, duration_minutes FROM lessons WHERE status != 'iptal'").fetchall()

    added_count = 0
    conflicts = []
    for i in range(weeks):
        current_start = start_dt + timedelta(weeks=i)
        current_end = current_start + timedelta(minutes=duration)

        conflict = False
        for el in existing_lessons:
            el_start = _parse_dt(el['lesson_date'])
            if not el_start:
                continue
            el_end = el_start + timedelta(minutes=el['duration_minutes'] or 60)
            if current_start < el_end and current_end > el_start:
                conflict = True
                break

        if conflict:
            conflicts.append(format_tarih(current_start))
            continue

        conn.execute('''
            INSERT INTO lessons (student_id, lesson_date, duration_minutes, status)
            VALUES (%s, %s, %s, %s)
        ''', (student_id, current_start.strftime('%Y-%m-%dT%H:%M'), duration, 'planlandi'))
        added_count += 1

    conn.commit()
    conn.close()

    if conflicts:
        flash(f'{len(conflicts)} ders çakıştığı için eklenmedi: {", ".join(conflicts[:4])}{"…" if len(conflicts) > 4 else ""}', 'warning')
    if added_count:
        flash(f'{added_count} ders takvime eklendi.', 'success')

    return redirect(url_for('schedule'))


@app.route('/schedule/update', methods=['POST'])
@admin_required
def update_lesson():
    lesson_id = request.form.get('lesson_id')
    status = request.form.get('status')
    topic = request.form.get('topic', '').strip()
    homework = request.form.get('homework', '').strip()
    notes = request.form.get('notes', '').strip()

    if status not in LESSON_STATUSES:
        flash('Geçersiz ders durumu.', 'danger')
        return redirect(url_for('schedule'))

    conn = get_db_connection()
    conn.execute('''
        UPDATE lessons
        SET status = %s, topic = %s, homework = %s, notes = %s
        WHERE id = %s
    ''', (status, topic, homework, notes, lesson_id))
    conn.commit()
    conn.close()

    flash('Ders kaydedildi.', 'success')
    return redirect(url_for('schedule'))


@app.route('/schedule/delete/<int:lesson_id>', methods=['POST'])
@admin_required
def delete_lesson(lesson_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM payments WHERE lesson_id = %s', (lesson_id,))
    conn.execute('DELETE FROM lessons WHERE id = %s', (lesson_id,))
    conn.commit()
    conn.close()
    flash('Ders takvimden silindi.', 'warning')
    return redirect(url_for('schedule'))


@app.route('/api/lessons/<int:lesson_id>/status', methods=['POST'])
@admin_required
def update_lesson_status_quick(lesson_id):
    data = request.get_json(silent=True) or {}
    new_status = data.get('status')
    if new_status not in LESSON_STATUSES:
        return jsonify({'success': False, 'error': 'Geçersiz durum'}), 400

    try:
        conn = get_db_connection()
        conn.execute('UPDATE lessons SET status = %s WHERE id = %s', (new_status, lesson_id))
        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.exception(e)
        return jsonify({'success': False, 'error': 'Kaydedilemedi, tekrar deneyin.'}), 500

    return jsonify({'success': True, 'new_status': new_status})


# --------------------------------------------------------------------------
# Ödemeler
# --------------------------------------------------------------------------
@app.route('/payments')
def payments():
    conn = get_db_connection()

    # Tamamlanmış ancak ödenmemiş dersler
    unpaid_lessons = conn.execute('''
        SELECT l.id as lesson_id, l.lesson_date, l.topic, s.id as student_id, s.full_name, s.hourly_rate
        FROM lessons l
        JOIN students s ON l.student_id = s.id
        WHERE l.status = 'yapildi'
        AND l.id NOT IN (SELECT lesson_id FROM payments WHERE is_paid = 1)
        ORDER BY l.lesson_date ASC
    ''').fetchall()

    # Son yapılan tahsilatlar
    paid_lessons = conn.execute('''
        SELECT p.id as payment_id, p.payment_date, p.amount, s.full_name, l.lesson_date
        FROM payments p
        JOIN lessons l ON p.lesson_id = l.id
        JOIN students s ON l.student_id = s.id
        WHERE p.is_paid = 1
        ORDER BY p.payment_date DESC LIMIT 20
    ''').fetchall()

    total_unpaid = sum(float(l['hourly_rate']) for l in unpaid_lessons)
    total_collected = sum(float(p['amount']) for p in paid_lessons)

    conn.close()
    return render_template('payments.html',
                           unpaid_lessons=unpaid_lessons,
                           paid_lessons=paid_lessons,
                           total_unpaid=total_unpaid,
                           total_collected=total_collected)


@app.route('/payments/pay/<int:lesson_id>', methods=['POST'])
@admin_required
def pay_lesson(lesson_id):
    conn = get_db_connection()
    lesson = conn.execute('''
        SELECT s.hourly_rate, s.full_name
        FROM lessons l
        JOIN students s ON l.student_id = s.id
        WHERE l.id = %s
    ''', (lesson_id,)).fetchone()
    already_paid = conn.execute(
        'SELECT 1 FROM payments WHERE lesson_id = %s AND is_paid = 1', (lesson_id,)
    ).fetchone()

    if not lesson:
        flash('Ders kaydı bulunamadı.', 'danger')
    elif already_paid:
        flash('Bu dersin ödemesi zaten alınmış.', 'info')
    else:
        payment_date = datetime.now().strftime('%Y-%m-%dT%H:%M')
        conn.execute('''
            INSERT INTO payments (lesson_id, amount, is_paid, payment_date)
            VALUES (%s, %s, 1, %s)
        ''', (lesson_id, float(lesson['hourly_rate']), payment_date))
        conn.commit()
        flash(f'{lesson["full_name"]} — {format_tl(lesson["hourly_rate"])} tahsil edildi.', 'success')

    conn.close()
    return redirect(url_for('payments'))


@app.route('/payments/undo/<int:payment_id>', methods=['POST'])
@admin_required
def undo_payment(payment_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM payments WHERE id = %s', (payment_id,))
    conn.commit()
    conn.close()
    flash('Tahsilat geri alındı, ders bekleyenlere döndü.', 'warning')
    return redirect(url_for('payments'))


# --------------------------------------------------------------------------
# Kaynaklar
# --------------------------------------------------------------------------
@app.route('/resources')
def resources():
    conn = get_db_connection()
    resources_data = conn.execute('SELECT * FROM resources ORDER BY grade_level ASC, title ASC').fetchall()
    conn.close()
    return render_template('resources.html', resources=resources_data)


@app.route('/resources/add', methods=['POST'])
@admin_required
def add_resource():
    title = request.form.get('title', '').strip()
    url = request.form.get('url', '').strip()
    grade_level = request.form.get('grade_level', '').strip()
    notes = request.form.get('notes', '').strip()

    if url and not url.lower().startswith(('http://', 'https://')):
        flash('Bağlantı http:// veya https:// ile başlamalı.', 'danger')
    elif not title:
        flash('Kaynak adı zorunlu.', 'danger')
    else:
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO resources (title, url, grade_level, notes)
            VALUES (%s, %s, %s, %s)
        ''', (title, url, grade_level, notes))
        conn.commit()
        conn.close()
        flash('Kaynak eklendi.', 'success')

    return redirect(url_for('resources'))


@app.route('/resources/delete/<int:id>', methods=['POST'])
@admin_required
def delete_resource(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM resources WHERE id = %s', (id,))
    conn.commit()
    conn.close()
    flash('Kaynak silindi.', 'warning')
    return redirect(url_for('resources'))


# --------------------------------------------------------------------------
# Notlar
# --------------------------------------------------------------------------
@app.route('/notes/add', methods=['POST'])
@admin_required
def add_note():
    content = request.form.get('content', '').strip()
    if not content:
        flash('Not boş olamaz.', 'danger')
    else:
        conn = get_db_connection()
        conn.execute('INSERT INTO quick_notes (content) VALUES (%s)', (content[:500],))
        conn.commit()
        conn.close()
    return redirect(url_for('index'))


@app.route('/notes/delete/<int:note_id>', methods=['POST'])
@admin_required
def delete_note(note_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM quick_notes WHERE id = %s', (note_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))


# --------------------------------------------------------------------------
# Hata sayfaları
# --------------------------------------------------------------------------
@app.errorhandler(400)
def bad_request(error):
    return render_template('error.html', code=400,
                           title='İstek geçersiz',
                           message='Oturum süresi dolmuş olabilir. Sayfayı yenileyip tekrar deneyin.'), 400


@app.errorhandler(404)
def not_found(error):
    return render_template('error.html', code=404,
                           title='Sayfa bulunamadı',
                           message='Aradığınız sayfa taşınmış ya da hiç var olmamış olabilir.'), 404


@app.errorhandler(500)
def internal_error(error):
    # Hata ayrıntısı sadece sunucu loglarına yazılır, kullanıcıya gösterilmez
    app.logger.exception(error)
    return render_template('error.html', code=500,
                           title='Bir şeyler ters gitti',
                           message='İşlem tamamlanamadı. Birkaç saniye sonra tekrar deneyin.'), 500


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=not IS_PRODUCTION)
