import os
import calendar
import urllib.parse
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from database import init_db, get_db_connection

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'mert_ders_takip_secret_2026_key')

# Tabloları başlat
init_db()

@app.route('/')
def index():
    conn = get_db_connection()
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    # Bugünkü dersler
    todays_lessons = conn.execute('''
        SELECT l.id, l.lesson_date, l.duration_minutes, l.status, l.topic, s.full_name, s.hourly_rate 
        FROM lessons l
        JOIN students s ON l.student_id = s.id
        WHERE substr(CAST(l.lesson_date AS text), 1, 10) = %s
        ORDER BY l.lesson_date ASC
    ''', (today_str,)).fetchall()
    
    # Bekleyen ödemeler toplamı (status = 'yapildi' ve ödenmemiş)
    pending_row = conn.execute('''
        SELECT SUM(s.hourly_rate) as total_debt 
        FROM lessons l
        JOIN students s ON l.student_id = s.id
        WHERE l.status = 'yapildi' 
        AND l.id NOT IN (SELECT lesson_id FROM payments WHERE is_paid = 1)
    ''').fetchone()
    total_pending = pending_row['total_debt'] if pending_row and pending_row['total_debt'] else 0
    
    # Son 6 ayın gelir istatistiği
    chart_labels = []
    chart_data = []
    today = datetime.now()
    month_names = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara']
    
    for i in range(5, -1, -1):
        m = today.month - i
        y = today.year
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
        chart_labels.append(f"{month_names[m-1]} {y}")
        chart_data.append(float(income))
        
    # Hızlı notlar
    quick_notes = conn.execute('SELECT * FROM quick_notes ORDER BY created_at DESC').fetchall()
    
    # Genel istatistikler (Öğrenci sayısı vb.)
    student_count_row = conn.execute('SELECT COUNT(*) as c FROM students').fetchone()
    student_count = student_count_row['c'] if student_count_row else 0
    
    conn.close()
    return render_template('index.html',
                           todays_lessons=todays_lessons,
                           total_pending=total_pending,
                           chart_labels=chart_labels,
                           chart_data=chart_data,
                           quick_notes=quick_notes,
                           student_count=student_count)

@app.route('/students')
def students():
    conn = get_db_connection()
    students_data = conn.execute('SELECT * FROM students ORDER BY full_name ASC').fetchall()
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
    
    # WhatsApp mesaj şablonu
    parent_clean = student['parent_contact'] or 'Velimiz'
    if pending_balance > 0:
        wa_text = f"Merhaba {parent_clean}, {student['full_name']} için tamamlanan {unpaid_count} dersin toplam {int(pending_balance) if pending_balance.is_integer() else pending_balance} TL bakiyesi bulunmaktadır. Bilgilerinize sunar, iyi günler dilerim."
    else:
        wa_text = f"Merhaba {parent_clean}, {student['full_name']} ile dersimizi başarıyla tamamladık. Öğrencimizin ders takibi günceldir. İyi günler dilerim."
        
    # Telefon numarasını temizle
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
                           pending_balance=pending_balance, 
                           wa_link=wa_link)

@app.route('/students/add', methods=['POST'])
def add_student():
    full_name = request.form.get('full_name', '').strip()
    grade_level = request.form.get('grade_level', '').strip()
    parent_contact = request.form.get('parent_contact', '').strip()
    hourly_rate = request.form.get('hourly_rate', '').strip()
    default_duration = request.form.get('default_duration', 60)
    notes = request.form.get('notes', '').strip()

    if not full_name or not hourly_rate:
        flash('Ad Soyad ve Saatlik Ders Ücreti zorunludur.', 'danger')
    else:
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO students (full_name, grade_level, parent_contact, hourly_rate, default_duration, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (full_name, grade_level, parent_contact, float(hourly_rate), int(default_duration or 60), notes))
        conn.commit()
        conn.close()
        flash(f'"{full_name}" başarıyla sisteme kaydedildi.', 'success')
        
    return redirect(url_for('students'))

@app.route('/students/edit/<int:id>', methods=['POST'])
def edit_student(id):
    full_name = request.form.get('full_name', '').strip()
    grade_level = request.form.get('grade_level', '').strip()
    parent_contact = request.form.get('parent_contact', '').strip()
    hourly_rate = request.form.get('hourly_rate', '').strip()
    default_duration = request.form.get('default_duration', 60)
    notes = request.form.get('notes', '').strip()

    if not full_name or not hourly_rate:
        flash('Ad Soyad ve Saatlik Ücret boş bırakılamaz.', 'danger')
    else:
        conn = get_db_connection()
        conn.execute('''
            UPDATE students 
            SET full_name = %s, grade_level = %s, parent_contact = %s, hourly_rate = %s, default_duration = %s, notes = %s
            WHERE id = %s
        ''', (full_name, grade_level, parent_contact, float(hourly_rate), int(default_duration or 60), notes, id))
        conn.commit()
        conn.close()
        flash('Öğrenci bilgileri güncellendi.', 'success')
        
    return redirect(request.referrer or url_for('students'))

@app.route('/students/delete/<int:id>', methods=['POST'])
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
        try:
            start_dt = datetime.strptime(row['lesson_date'], '%Y-%m-%dT%H:%M')
            duration = row['duration_minutes'] or 60
            end_dt = start_dt + timedelta(minutes=duration)
            
            # Durum renkleri - Modern Dark Slate & Neon Accent Palette
            if row['status'] == 'yapildi':
                color = '#10B981' # Emerald Green
            elif row['status'] == 'iptal':
                color = '#F43F5E' # Coral Rose
            else:
                color = '#0EA5E9' # Vibrant Sky/Cyan for 'planlandi'

            events.append({
                'id': row['id'],
                'title': f"{row['full_name']}",
                'start': start_dt.isoformat(),
                'end': end_dt.isoformat(),
                'color': color,
                'extendedProps': {
                    'status': row['status'],
                    'studentName': row['full_name'],
                    'duration': duration,
                    'topic': row['topic'] or '',
                    'homework': row['homework'] or '',
                    'notes': row['notes'] or ''
                }
            })
        except Exception:
            continue

    return jsonify(events)

@app.route('/schedule/add', methods=['POST'])
def add_lesson():
    student_id = request.form.get('student_id')
    lesson_date = request.form.get('lesson_date') # YYYY-MM-DDTHH:MM
    weeks = int(request.form.get('weeks', 1))

    if not student_id or not lesson_date:
        flash('Öğrenci ve Tarih seçimi zorunludur.', 'danger')
        return redirect(url_for('schedule'))

    try:
        start_dt = datetime.strptime(lesson_date, '%Y-%m-%dT%H:%M')
    except ValueError:
        flash('Geçersiz tarih formatı.', 'danger')
        return redirect(url_for('schedule'))

    conn = get_db_connection()
    student = conn.execute('SELECT default_duration FROM students WHERE id = %s', (student_id,)).fetchone()
    duration = student['default_duration'] if student and student['default_duration'] else 60
    
    existing_lessons = conn.execute("SELECT lesson_date, duration_minutes FROM lessons WHERE status != 'iptal'").fetchall()
    
    added_count = 0
    conflict_count = 0
    for i in range(weeks):
        current_start = start_dt + timedelta(weeks=i)
        current_end = current_start + timedelta(minutes=duration)
        
        conflict = False
        for el in existing_lessons:
            try:
                el_start = datetime.strptime(el['lesson_date'], '%Y-%m-%dT%H:%M')
                el_end = el_start + timedelta(minutes=el['duration_minutes'] or 60)
                if current_start < el_end and current_end > el_start:
                    conflict = True
                    break
            except Exception:
                continue
                
        if conflict:
            conflict_count += 1
            continue
            
        conn.execute('''
            INSERT INTO lessons (student_id, lesson_date, duration_minutes, status)
            VALUES (%s, %s, %s, %s)
        ''', (student_id, current_start.strftime('%Y-%m-%dT%H:%M'), duration, 'planlandi'))
        added_count += 1
        
    conn.commit()
    conn.close()
    
    if conflict_count > 0:
        flash(f'{conflict_count} adet ders çakışma nedeniyle eklenemedi.', 'warning')
    if added_count > 0:
        flash(f'{added_count} adet ders başarıyla takvime eklendi.', 'success')
        
    return redirect(url_for('schedule'))

@app.route('/schedule/update', methods=['POST'])
def update_lesson():
    lesson_id = request.form.get('lesson_id')
    status = request.form.get('status')
    topic = request.form.get('topic', '').strip()
    homework = request.form.get('homework', '').strip()
    notes = request.form.get('notes', '').strip()

    conn = get_db_connection()
    conn.execute('''
        UPDATE lessons
        SET status = %s, topic = %s, homework = %s, notes = %s
        WHERE id = %s
    ''', (status, topic, homework, notes, lesson_id))
    conn.commit()
    conn.close()
    
    flash('Ders detayları kaydedildi.', 'success')
    return redirect(url_for('schedule'))

@app.route('/schedule/delete/<int:lesson_id>', methods=['POST'])
def delete_lesson(lesson_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM payments WHERE lesson_id = %s', (lesson_id,))
    conn.execute('DELETE FROM lessons WHERE id = %s', (lesson_id,))
    conn.commit()
    conn.close()
    flash('Ders takvimden silindi.', 'warning')
    return redirect(url_for('schedule'))

@app.route('/api/lessons/<int:lesson_id>/status', methods=['POST'])
def update_lesson_status_quick(lesson_id):
    try:
        data = request.get_json() or {}
        new_status = data.get('status')
        if new_status not in ['planlandi', 'yapildi', 'iptal']:
            return jsonify({'success': False, 'error': 'Geçersiz durum'}), 400
            
        conn = get_db_connection()
        conn.execute('UPDATE lessons SET status = %s WHERE id = %s', (new_status, lesson_id))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'new_status': new_status})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/payments')
def payments():
    conn = get_db_connection()
    
    # Tamamlanmış ancak ödenmemiş dersler
    unpaid_lessons = conn.execute('''
        SELECT l.id as lesson_id, l.lesson_date, l.topic, s.full_name, s.hourly_rate 
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
def pay_lesson(lesson_id):
    conn = get_db_connection()
    lesson = conn.execute('''
        SELECT s.hourly_rate 
        FROM lessons l 
        JOIN students s ON l.student_id = s.id 
        WHERE l.id = %s
    ''', (lesson_id,)).fetchone()
    
    if lesson:
        payment_date = datetime.now().strftime('%Y-%m-%dT%H:%M')
        conn.execute('''
            INSERT INTO payments (lesson_id, amount, is_paid, payment_date)
            VALUES (%s, %s, 1, %s)
        ''', (lesson_id, float(lesson['hourly_rate']), payment_date))
        conn.commit()
        flash('Tahsilat başarıyla kaydedildi!', 'success')
    else:
        flash('Ders kaydı bulunamadı.', 'danger')
        
    conn.close()
    return redirect(url_for('payments'))

@app.route('/payments/undo/<int:payment_id>', methods=['POST'])
def undo_payment(payment_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM payments WHERE id = %s', (payment_id,))
    conn.commit()
    conn.close()
    flash('Tahsilat işlemi geri alındı (ödenmemişlere aktarıldı).', 'warning')
    return redirect(url_for('payments'))

@app.route('/resources')
def resources():
    conn = get_db_connection()
    resources_data = conn.execute('SELECT * FROM resources ORDER BY grade_level ASC, title ASC').fetchall()
    conn.close()
    return render_template('resources.html', resources=resources_data)

@app.route('/resources/add', methods=['POST'])
def add_resource():
    title = request.form.get('title', '').strip()
    url = request.form.get('url', '').strip()
    grade_level = request.form.get('grade_level', '').strip()
    notes = request.form.get('notes', '').strip()

    if not title:
        flash('Kaynak adı zorunludur.', 'danger')
    else:
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO resources (title, url, grade_level, notes)
            VALUES (%s, %s, %s, %s)
        ''', (title, url, grade_level, notes))
        conn.commit()
        conn.close()
        flash('Kaynak materyali eklendi.', 'success')
        
    return redirect(url_for('resources'))

@app.route('/resources/delete/<int:id>', methods=['POST'])
def delete_resource(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM resources WHERE id = %s', (id,))
    conn.commit()
    conn.close()
    flash('Kaynak silindi.', 'warning')
    return redirect(url_for('resources'))

@app.route('/notes/add', methods=['POST'])
def add_note():
    content = request.form.get('content', '').strip()
    if not content:
        flash('Not içeriği boş olamaz.', 'danger')
    else:
        conn = get_db_connection()
        conn.execute('INSERT INTO quick_notes (content) VALUES (%s)', (content,))
        conn.commit()
        conn.close()
        flash('Yeni not eklendi.', 'success')
    return redirect(url_for('index'))

@app.route('/notes/delete/<int:note_id>', methods=['POST'])
def delete_note(note_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM quick_notes WHERE id = %s', (note_id,))
    conn.commit()
    conn.close()
    flash('Not silindi.', 'info')
    return redirect(url_for('index'))

@app.errorhandler(500)
def internal_error(error):
    import traceback
    err_msg = traceback.format_exc()
    print("500 ERROR TRACEBACK:\n", err_msg)
    return f"""
    <div style='background:#0B0F17; color:#F8FAFC; padding:40px; font-family:sans-serif;'>
        <h2 style='color:#F43F5E;'>Uygulama Hatası (500)</h2>
        <p>Aşağıdaki hata detayını inceleyebilirsiniz:</p>
        <pre style='background:#141E33; padding:20px; border-radius:12px; color:#38BDF8; overflow:auto;'>{err_msg}</pre>
    </div>
    """, 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
