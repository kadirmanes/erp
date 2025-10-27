from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, make_response, Response, send_file, session
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
from mysql.connector import Error
import os
from datetime import datetime
import threading
import pdfkit
from weasyprint import HTML
import qrcode
import base64
from io import BytesIO
from functools import wraps
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=3600  # 1 saat
)


# ESKİ login_required FONKSİYONUNUZU BU KOD İLE DEĞİŞTİRİN

def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'username' not in session:
                return redirect(url_for('login'))
            
            if role and session.get('rol') != role:
                return "Yetkisiz erişim", 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME")
        )
        return conn
    except mysql.connector.Error as err:
        app.logger.error(f"Veritabanı bağlantı hatası: {err}")
        return None

# MANES numarası oluşturma (thread-safe)
manes_lock = threading.Lock()
def get_manes_numara():
    with manes_lock:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT manes_numara FROM talepler ORDER BY LENGTH(manes_numara) DESC, manes_numara DESC LIMIT 1")
        max_numara = cursor.fetchone()
        conn.close()

        if not max_numara or not max_numara['manes_numara']:
            return "MANES2024-1"
        
        try:
            parts = max_numara['manes_numara'].split('-')
            numara = int(parts[1]) + 1
            return f"MANES2024-{numara}"
        except (IndexError, ValueError) as e:
            print(f"Hata oluştu: {e}")
            return "MANES2024-1"

def saat_to_dakika(saat_str):
    try:
        saat = float(saat_str.replace(',', '.'))
        return int(saat * 60)
    except:
        return 0
# Başlangıç sayfası
@app.route('/')
@login_required()
def home():
    return render_template('anasayfa.html')
@app.route('/musteriler')
@login_required()
def musteriler():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)  # Satırları sözlük olarak almak için
        cursor.execute('SELECT * FROM musteriler')
        musteriler = cursor.fetchall()
        cursor.close()
        conn.close()
        return render_template('musteriler.html', musteriler=musteriler)
    except Exception as e:
        flash(f"Veritabanı hatası: {e}", "error")
        return render_template('musteriler.html', musteriler=[])
@app.route('/musteri/<int:id>')
@login_required()
def musteri_detay(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)  # Satırları sözlük olarak almak için

        # Müşteri bilgilerini al
        cursor.execute('SELECT * FROM musteriler WHERE id = %s', (id,))
        musteri = cursor.fetchone()

        if not musteri:
            conn.close()
            flash("Müşteri bulunamadı!", "error")
            return f"ID {id} için müşteri bulunamadı 😕"

        # Müşteriye ait siparişleri çek
        cursor.execute('SELECT * FROM siparisler WHERE musteri = %s', (musteri["firma_unvani"],))
        siparisler = cursor.fetchall()

        # Müşteriye ait teklifler
        cursor.execute('SELECT * FROM teklifler WHERE musteri = %s', (musteri["firma_unvani"],))
        teklifler = cursor.fetchall()

        # Üretim durumu
        cursor.execute('SELECT * FROM üretim_durum WHERE musteri = %s', (musteri["firma_unvani"],))
        üretim_durum = cursor.fetchall()

        cursor.close()
        conn.close()

        return render_template('musteri_detay.html',
                               musteri=musteri,
                               siparisler=siparisler,
                               teklifler=teklifler,
                               üretim_durum=üretim_durum)

    except Exception as e:
        flash(f"Veritabanı hatası: {e}", "error")
        return redirect(url_for('musteriler'))
@app.route('/musteri_ekle', methods=['GET', 'POST'])
@login_required()()
def musteri_ekle():
    if request.method == 'POST':
        try:
            firma_unvani = request.form['firma_unvani']
            email = request.form['email']
            telefon = request.form['telefon']
            vkn_tckn = request.form['vkn_tckn']
            vergi_dairesi = request.form['vergi_dairesi']
            adres = request.form['adres']

            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            # Vergi numarası daha önce kayıtlı mı kontrol et
            cursor.execute('SELECT * FROM musteriler WHERE vkn_tckn = %s', (vkn_tckn,))
            existing_musteri = cursor.fetchone()

            if existing_musteri:
                cursor.close()
                conn.close()
                return jsonify({"error": "Bu vergi kimlik numarası zaten kayıtlı!"})

            # Yeni müşteri ekle
            cursor.execute(
                '''
                INSERT INTO musteriler (firma_unvani, email, telefon, adres, vkn_tckn, vergi_dairesi)
                VALUES (%s, %s, %s, %s, %s, %s)
                ''',
                (firma_unvani, email, telefon, adres, vkn_tckn, vergi_dairesi)
            )
            conn.commit()
            cursor.close()
            conn.close()

            return jsonify({
                "message": "Müşteri başarıyla eklendi!",
                "redirect": url_for('musteriler')
            })

        except Exception as e:
            return jsonify({"error": f"Veritabanı hatası: {e}"})

    # GET isteği ise formu göster
    return render_template('musteri_ekle.html')
@app.route('/musteri_sil/<int:id>', methods=['GET'])
@login_required()()
def musteri_sil(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Müşteri var mı kontrol et
        cursor.execute('SELECT * FROM musteriler WHERE id = %s', (id,))
        musteri = cursor.fetchone()

        if not musteri:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "Müşteri bulunamadı!"})

        # Müşteri sil
        cursor.execute('DELETE FROM musteriler WHERE id = %s', (id,))
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Müşteri başarıyla silindi!",
            "redirect": url_for('musteriler')
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Veritabanı hatası: {e}"
        })
@app.route('/musteri_duzenle/<int:id>', methods=['GET', 'POST'])
@login_required()()
def musteri_duzenle(id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Mevcut müşteri verisini al
    cursor.execute('SELECT * FROM musteriler WHERE id = %s', (id,))
    musteri = cursor.fetchone()

    if not musteri:
        cursor.close()
        conn.close()
        return jsonify({"error": "Müşteri bulunamadı!"}), 404

    if request.method == 'POST':
        try:
            firma_unvani = request.form['firma_unvani']
            email = request.form['email']
            telefon = request.form['telefon']
            adres = request.form['adres']
            vkn_tckn = request.form['vkn_tckn']
            vergi_dairesi = request.form['vergi_dairesi']

            cursor.execute(
                '''
                UPDATE musteriler 
                SET firma_unvani = %s, email = %s, telefon = %s, adres = %s, vkn_tckn = %s, vergi_dairesi = %s 
                WHERE id = %s
                ''', (firma_unvani, email, telefon, adres, vkn_tckn, vergi_dairesi, id)
            )
            conn.commit()
            cursor.close()
            conn.close()

            return jsonify({
                "message": "Müşteri başarıyla güncellendi!",
                "redirect": url_for('musteriler')
            })

        except Exception as e:
            cursor.close()
            conn.close()
            return jsonify({"error": f"Veritabanı hatası: {e}"}), 500

    cursor.close()
    conn.close()
    return render_template('musteri_duzenle.html', musteri=musteri)
@app.route('/talepler')
@login_required()()
def talepler():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql_query = '''
            SELECT talepler.id, 
                   talepler.manes_numara, 
                   talepler.tarih, 
                   talepler.musteri_no, 
                   musteriler.firma_unvani, 
                   talepler.musteri_id,
                   (SELECT id FROM teklifler WHERE teklifler.teklif_ismi = talepler.musteri_no LIMIT 1) AS teklif_id
            FROM talepler
            JOIN musteriler ON talepler.musteri_id = musteriler.id
        '''

        cursor.execute(sql_query)
        talepler = cursor.fetchall()
        cursor.close()
        conn.close()



        return render_template('talepler.html', talepler=talepler)

    except Exception as e:
        flash(f"Veritabanı hatası: {e}", "error")
        return render_template('talepler.html', talepler=[])
@app.route('/talep_olustur', methods=['GET', 'POST'])
@login_required()()
def talep_olustur():
    if request.method == 'POST':
        try:
            musteri_no = request.form['musteri_no']
            musteri_id = request.form['musteri_id']
            manes_numara = get_manes_numara()
            tarih = datetime.now().strftime('%Y-%m-%d')

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO talepler (musteri_id, musteri_no, manes_numara, tarih)
                VALUES (%s, %s, %s, %s)
                ''', (musteri_id, musteri_no, manes_numara, tarih)
            )
            conn.commit()
            cursor.close()
            conn.close()

            # Klasör oluşturma
            klasor_yolu = os.path.join(app.root_path, 'manusystem', manes_numara)
            try:
                os.makedirs(klasor_yolu, exist_ok=True)
            except OSError as e:
                return jsonify({"error": f"Klasör oluşturma hatası: {e}"}), 500

            return jsonify({
                "message": "Talep başarıyla oluşturuldu!",
                "redirect": url_for('talepler')
            })

        except Exception as e:
            return jsonify({"error": f"Hata: {e}"}), 500

    # GET isteği için müşteri listesini getir
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM musteriler')
        musteriler = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as e:
        return jsonify({"error": f"Veritabanı hatası: {e}"}), 500

    manes_numara = get_manes_numara()
    return render_template('talep_olustur.html', manes_numara=manes_numara, musteriler=musteriler)
@app.route('/talep/<int:id>')
@login_required()()
def talep_detay(id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        '''SELECT t.*, m.firma_unvani, m.email, m.telefon
           FROM talepler t
           JOIN musteriler m ON t.musteri_id = m.id
           WHERE t.id = %s''',
        (id,)
    )
    talep = cursor.fetchone()

    cursor.close()
    conn.close()

    if not talep:
        return "Talep bulunamadı!", 404

    return render_template('talep_detay.html', talep=talep)
@app.route('/talep_duzenle/<int:id>', methods=['GET', 'POST'])
@login_required()()
def talep_duzenle(id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Talep bilgilerini çek
    cursor.execute('SELECT * FROM talepler WHERE id = %s', (id,))
    talep = cursor.fetchone()

    cursor.execute('SELECT * FROM musteriler')
    musteriler = cursor.fetchall()

    if not talep:
        cursor.close()
        conn.close()
        return jsonify({"error": "Talep bulunamadı!"}), 400

    if request.method == 'POST':
        try:
            musteri_no = request.form['musteri_no']
            musteri_id = request.form['musteri_id']
            tarih = datetime.now().strftime('%Y-%m-%d')

            # MANES numarasını değiştirmeden al
            manes_numara = talep['manes_numara']

            cursor.execute(
                '''
                UPDATE talepler 
                SET musteri_no = %s, musteri_id = %s, manes_numara = %s, tarih = %s
                WHERE id = %s
                ''',
                (musteri_no, musteri_id, manes_numara, tarih, id)
            )
            conn.commit()
            cursor.close()
            conn.close()

            return jsonify({
                "message": "Talep başarıyla güncellendi!",
                "redirect": url_for('talepler')
            })

        except mysql.connector.Error as e:
            cursor.close()
            conn.close()
            return jsonify({"error": f"Veritabanı hatası: {str(e)}"}), 500

    cursor.close()
    conn.close()
    return render_template('talep_duzenle.html', talep=talep, musteriler=musteriler)
@app.route('/talep_sil/<int:id>', methods=['GET'])
@login_required()()()
def talep_sil(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute('SELECT * FROM talepler WHERE id = %s', (id,))
        musteri = cursor.fetchone()

        if not musteri:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "Talep bulunamadı!"})

        cursor.execute('DELETE FROM talepler WHERE id = %s', (id,))
        conn.commit()

        cursor.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Talep başarıyla silindi!",
            "redirect": url_for('talepler')
        })

    except mysql.connector.Error as e:
        return jsonify({
            "success": False,
            "message": f"Veritabanı hatası: {str(e)}"
        })
@app.route('/teklif_olustur', methods=['GET'])
@login_required()
def teklif_olustur():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute('''
        SELECT t.id, t.manes_numara, m.firma_unvani, t.musteri_no
        FROM talepler t
        JOIN musteriler m ON t.musteri_id = m.id
    ''')
    talepler = cursor.fetchall()
    
    cursor.close()
    conn.close()

    teklif_seri = get_teklif_seri()  # Teklif numarası üretme fonksiyonu
    return render_template('teklif_olustur.html', talepler=talepler, teklif_seri=teklif_seri)
def get_teklif_seri():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    current_year = datetime.now().year
    prefix = f"MNS{current_year}-"

    cursor.execute("SELECT teklif_ismi FROM teklifler WHERE teklif_ismi LIKE %s", (f"{prefix}%",))
    teklifler = cursor.fetchall()

    cursor.close()
    conn.close()

    numbers = []
    for teklif in teklifler:
        try:
            suffix = teklif["teklif_ismi"].split("-")[-1]
            numbers.append(int(suffix))
        except:
            continue

    next_number = max(numbers) + 1 if numbers else 1
    return f"{prefix}{str(next_number).zfill(4)}"
@app.route('/teklifler')
@login_required()
def teklifler():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute('''
        SELECT t.id, t.teklif_ismi, t.musteri, t.duzenleme_tarihi, 
               t.genel_toplam, t.vade_tarihi, t.doviz, t.durum,
               tp.musteri_no
        FROM teklifler t
        LEFT JOIN talepler tp ON t.talep_id = tp.id
    ''')
    teklifler = cursor.fetchall()

    cursor.close()
    conn.close()

    teklif_listesi = []
    for teklif in teklifler:
        teklif['genel_toplam'] = float(teklif['genel_toplam'] or 0)
        teklif_listesi.append(teklif)

   

    return render_template('teklifler.html', teklifler=teklif_listesi)
@app.route('/teklif/<int:id>')
@login_required()
def teklif_detay(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Teklif ana bilgilerini çek + talep numarasını (manes_numara) da getiriyoruz
        cursor.execute('''
            SELECT t.*, tp.manes_numara
            FROM teklifler t
            LEFT JOIN talepler tp ON t.talep_id = tp.id
            WHERE t.id = %s
        ''', (id,))
        teklif = cursor.fetchone()

        if not teklif:
            cursor.close()
            conn.close()
            flash("Teklif bulunamadı!", "error")
            return redirect(url_for('teklifler'))

        # Detay satırlarını al
        cursor.execute('SELECT * FROM teklif_detay WHERE teklif_id = %s', (id,))
        detaylar = cursor.fetchall()

        cursor.close()
        conn.close()

        return render_template("teklif_detay.html", teklif=teklif, detaylar=detaylar)

    except Exception as e:
        print(f"Teklif detay görüntüleme hatası: {e}")
        flash("Bir hata oluştu!", "error")
        return redirect(url_for('teklifler'))
@app.route('/teklif_kaydet', methods=['POST'])
@login_required()
def teklif_kaydet():
    try:
        talep_id = request.form.get('talep_id')
        if not talep_id:
            return jsonify({"error": "Talep seçilmedi!"}), 400

        try:
            talep_id = int(talep_id)
        except ValueError:
            return jsonify({"error": "Talep ID geçersiz!"}), 400

        teklif_ismi = request.form.get('teklif_ismi')
        musteri = request.form.get('musteri')
        duzenleme_tarihi = request.form.get('duzenleme_tarihi')
        vade_tarihi = request.form.get('vade_tarihi')
        genel_toplam = request.form.get('genel_toplam', '0').strip()
        doviz = request.form.get('doviz', 'TL')

        if "," in genel_toplam and "." in genel_toplam:
            genel_toplam = genel_toplam.replace(".", "").replace(",", ".")
        elif "," in genel_toplam:
            genel_toplam = genel_toplam.replace(",", ".")

        try:
            genel_toplam = float(genel_toplam)
        except ValueError:
            return jsonify({"error": "Genel toplam geçersiz!"}), 400

        if not (teklif_ismi and musteri and duzenleme_tarihi and genel_toplam > 0):
            return jsonify({"error": "Eksik teklif bilgisi! Lütfen zorunlu alanları doldurun."}), 400

        urunler = request.form.getlist('urun[]')
        miktarlar = request.form.getlist('miktar[]')
        birimler = request.form.getlist('birim[]')
        birim_fiyatlar = request.form.getlist('birim_fiyat[]')
        kdv_oranlar = request.form.getlist('kdv_oran[]')

        if not urunler or not miktarlar or not birim_fiyatlar:
            return jsonify({"error": "Ürün detayları eksik! Lütfen tüm satırları doldurun."}), 400

        for i in range(len(urunler)):
            if not urunler[i].strip():
                return jsonify({"error": "Hizmet adı boş olamaz!"}), 400
            if not miktarlar[i].strip() or float(miktarlar[i].replace(".", "").replace(",", ".")) <= 0:
                return jsonify({"error": "Miktar boş veya geçersiz!"}), 400
            if not birim_fiyatlar[i].strip() or float(birim_fiyatlar[i].replace(".", "").replace(",", ".")) <= 0:
                return jsonify({"error": "Birim fiyat boş veya geçersiz!"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO teklifler (teklif_ismi, musteri, duzenleme_tarihi, vade_tarihi, genel_toplam, doviz, talep_id, durum)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ''', (teklif_ismi, musteri, duzenleme_tarihi, vade_tarihi, genel_toplam, doviz, talep_id, "bekliyor"))

        teklif_id = cursor.lastrowid

        for i in range(len(urunler)):
            urun = urunler[i]
            miktar = float(miktarlar[i].replace(".", "").replace(",", "."))
            birim_fiyat = float(birim_fiyatlar[i].replace(".", "").replace(",", "."))
            birim = birimler[i]
            kdv_oran = float(kdv_oranlar[i].replace("%", "").replace(",", "."))
            toplam = miktar * birim_fiyat * (1 + kdv_oran)

            cursor.execute('''
                INSERT INTO teklif_detay (teklif_id, urun, miktar, birim, birim_fiyat, vade_tarihi, kdv_oran, toplam)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (teklif_id, urun, miktar, birim, birim_fiyat, vade_tarihi, kdv_oran, toplam))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "message": "Teklif başarıyla kaydedildi!",
            "redirect": url_for('teklifler')
        })

    except Exception as e:
        print(f"❌ Hata oluştu: {str(e)}")
        return jsonify({"error": f"Hata oluştu: {str(e)}"}), 500
@app.route('/teklif_duzenle/<int:id>', methods=['GET', 'POST'])
@login_required()
def teklif_duzenle(id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Teklif ana bilgilerini çek
    cursor.execute('SELECT * FROM teklifler WHERE id = %s', (id,))
    teklif = cursor.fetchone()

    # Teklif bulunamadıysa
    if not teklif:
        cursor.close()
        conn.close()
        flash("Teklif bulunamadı!", "error")
        return redirect(url_for('teklifler'))

    # Teklif detaylarını çek
    cursor.execute('SELECT * FROM teklif_detay WHERE teklif_id = %s', (id,))
    teklif_detaylari = cursor.fetchall()

    # Detayları frontend'e uygun formata dönüştür
    detay_listesi = []
    for detay in teklif_detaylari:
        detay_listesi.append({
            'urun': detay['urun'],
            'miktar': str(detay['miktar']).replace(".", ","),
            'birim': detay['birim'],
            'birim_fiyat': str(detay['birim_fiyat']).replace(".", ","),
            'kdv_oran': float(detay['kdv_oran']),
            'toplam': str(detay['toplam']).replace(".", ",")
        })

    vade_tarihi = teklif['vade_tarihi'] if teklif['vade_tarihi'] else ''
    doviz = teklif['doviz'] if teklif['doviz'] else '₺'

    cursor.close()
    conn.close()

    return render_template(
        'teklif_duzenle.html',
        teklif=teklif,
        teklif_detaylari=detay_listesi,
        vade_tarihi=vade_tarihi,
        doviz=doviz
    )
@app.route('/teklif_guncelle/<int:id>', methods=['POST'])
@login_required()
def teklif_guncelle(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Teklif ana bilgilerini güncelle
        teklif_ismi = request.form.get('teklif_ismi')
        musteri = request.form.get('musteri')
        duzenleme_tarihi = request.form.get('duzenleme_tarihi')
        genel_toplam = request.form.get('genel_toplam', '0').replace(",", ".")
        doviz = request.form.get('doviz', '₺')
        vade_tarihi = request.form.get('vade_tarihi', '')

        cursor.execute(
            '''
            UPDATE teklifler 
            SET teklif_ismi = %s, musteri = %s, duzenleme_tarihi = %s, genel_toplam = %s, doviz = %s, vade_tarihi = %s
            WHERE id = %s
        ''', (teklif_ismi, musteri, duzenleme_tarihi, genel_toplam, doviz, vade_tarihi, id)
        )

        # Mevcut teklif detaylarını sil
        cursor.execute('DELETE FROM teklif_detay WHERE teklif_id = %s', (id, ))

        # Yeni teklif detaylarını al
        urunler = request.form.getlist('urun[]')
        miktarlar = request.form.getlist('miktar[]')
        birimler = request.form.getlist('birim[]')
        birim_fiyatlar = request.form.getlist('birim_fiyat[]')
        kdv_oranlar = request.form.getlist('kdv_oran[]')

        for i in range(len(urunler)):
            urun = urunler[i]
            miktar = float(miktarlar[i].replace(",", ".")) if miktarlar[i] else 0
            birim = birimler[i]
            birim_fiyat = float(birim_fiyatlar[i].replace(",", ".")) if birim_fiyatlar[i] else 0
            kdv_oran = float(kdv_oranlar[i].replace(",", ".")) if kdv_oranlar[i] else 0
            toplam = miktar * birim_fiyat * (1 + kdv_oran)

            cursor.execute(
                '''
                INSERT INTO teklif_detay (teklif_id, urun, miktar, birim, birim_fiyat, kdv_oran, toplam)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (id, urun, miktar, birim, birim_fiyat, kdv_oran, toplam)
            )

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"message": "Teklif başarıyla güncellendi!", "redirect": url_for('teklifler')})

    except Exception as e:
        return jsonify({"error": f"Hata oluştu: {str(e)}"}), 500

@app.route('/teklif_durum_guncelle/<int:id>', methods=['POST'])
@login_required()
def teklif_durum_guncelle(id):
    yeni_durum = request.form.get('durum')

    if yeni_durum not in ['bekliyor', 'onaylandi', 'reddedildi']:
        flash("Geçersiz durum!", "error")
        return redirect(url_for('teklifler'))

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE teklifler SET durum = %s WHERE id = %s", (yeni_durum, id))
        conn.commit()
        cursor.close()
        conn.close()
        flash("Teklif durumu güncellendi ✅", "success")
    except Exception as e:
        flash(f"Hata: {e}", "error")

    return redirect(url_for('teklifler'))

@app.route('/teklif_sil/<int:id>', methods=['GET'])
@login_required()
def teklif_sil(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute('SELECT * FROM teklifler WHERE id = %s', (id,))
        musteri = cursor.fetchone()

        if not musteri:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "Teklif bulunamadı!"})

        cursor.execute('DELETE FROM teklifler WHERE id = %s', (id,))
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Teklif başarıyla silindi!",
            "redirect": url_for('teklifler')
        })

    except mysql.connector.Error as e:
        return jsonify({"success": False, "message": f"Veritabanı hatası: {e}"})
        
@app.route('/siparisler')
@login_required()
def siparisler():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM siparisler')
    siparisler = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('siparisler.html', siparisler=siparisler)
@app.route('/raporlar')
@login_required()
def raporlar():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM raporlar')
    raporlar = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('raporlar.html', raporlar=raporlar)

@app.route('/siparis_olustur/<int:id>', methods=['GET', 'POST'])
@login_required()
def siparis_olustur(id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute('SELECT * FROM teklifler WHERE id = %s', (id,))
    teklif = cursor.fetchone()

    if not teklif:
        cursor.close()
        conn.close()
        flash("Sipariş oluşturulacak teklif bulunamadı!", "error")
        return redirect(url_for('teklifler'))

    if request.method == 'POST':
        siparis_ismi = request.form['siparis_ismi']
        musteri = request.form['musteri']
        siparis_tarihi = request.form['siparis_tarihi']
        toplam_tutar = request.form['toplam_tutar']

        cursor.execute('''
            INSERT INTO siparisler (siparis_ismi, musteri, siparis_tarihi, toplam_tutar)
            VALUES (%s, %s, %s, %s)
        ''', (siparis_ismi, musteri, siparis_tarihi, toplam_tutar))

        conn.commit()
        cursor.close()
        conn.close()
        flash("Sipariş başarıyla oluşturuldu!", "success")
        return redirect(url_for('siparisler'))

    cursor.close()
    conn.close()
    return render_template('siparis_olustur.html', teklif=teklif)

@app.route('/api/talepler', methods=['GET'])
@login_required()
def api_talepler():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM talepler')
        talepler = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify(talepler)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/musteriler', methods=['GET'])
@login_required()
def api_musteriler():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM musteriler')
        musteriler = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify(musteriler)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/teklif_pdf/<int:id>')
@login_required()
def teklif_pdf(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Teklif ve detay verilerini al
        cursor.execute('''
            SELECT t.*, tp.manes_numara
            FROM teklifler t
            LEFT JOIN talepler tp ON t.talep_id = tp.id
            WHERE t.id = %s
        ''', (id,))
        teklif = cursor.fetchone()

        cursor.execute('SELECT * FROM teklif_detay WHERE teklif_id = %s', (id,))
        detaylar = cursor.fetchall()

        cursor.close()
        conn.close()

        if not teklif:
            return "Teklif bulunamadı", 404

        rendered = render_template("teklif_pdf.html", teklif=teklif, detaylar=detaylar)
        
        config = pdfkit.configuration(wkhtmltopdf=r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe')
        pdf = pdfkit.from_string(rendered, False, configuration=config)
        response = make_response(pdf)
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = f"inline; filename={teklif['teklif_ismi']}.pdf"
        return response

    except Exception as e:
        return f"Hata: {str(e)}", 500


def generate_is_emri_no():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_emri_no FROM uretim ORDER BY id DESC LIMIT 1")
    last = cursor.fetchone()
    conn.close()
    if last and last[0].startswith("M"):
        last_number = int(last[0][1:])
        new_number = last_number + 1
    else:
        new_number = 1
    return f"M{new_number:04d}"

@app.route('/uretim_olustur', methods=['GET', 'POST'])
@login_required()
def uretim_olustur():
    
    if request.method == 'POST':
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            is_emri_no = request.form.get('is_emri_no')  # readonly inputtan geliyor

            # Formdan gelen iş emri no; formda readonly veya hidden input olarak yer almalı.
            alt_resim_nolar = request.form.getlist('resim_no_list[]')
            alt_adetler = request.form.getlist('adet_list[]')
            alt_tahmini_sureler = request.form.getlist('sure_list[]')
            alt_aciklamalar = request.form.getlist('aciklama_list[]')
            adet = request.form.get('adet')
            aciklama = request.form.get('aciklama')
            talep_id_raw = request.form.get('talep_id')  # önce formdan gelen değeri al
            musteri_id = None
            talep_id = None
            if talep_id_raw == "manuel":
                talep_id = None
                musteri_id = request.form.get('musteri_id') or None
            elif talep_id_raw:
                talep_id = talep_id_raw
                cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT musteri_id FROM talepler WHERE id = %s", (talep_id,))
                result = cursor.fetchone()
                if result:
                    musteri_id = result['musteri_id']


            
            total_planlanan_dakika = 0
            is_emirleri_data = []
            for i in range(len(alt_resim_nolar)):
                tahmini_sure = float(alt_tahmini_sureler[i]) if alt_tahmini_sureler[i] else 0
                adet = int(alt_adetler[i]) if alt_adetler[i] else 0
                # Saat cinsinden girilen süreyi dakikaya çevirip, adet ile çarpıyoruz.
                dakika = saat_to_dakika(str(tahmini_sure)) * adet
                total_planlanan_dakika += dakika
                is_emirleri_data.append({
                    "resim_no": alt_resim_nolar[i],
                    "adet": adet,
                    "tahmini_sure": tahmini_sure,       # saat cinsinden orijinal süre
                    "planlanan_dakika": dakika,           # hesaplanmış dakika
                    "aciklama": alt_aciklamalar[i] if i < len(alt_aciklamalar) else '',
                    "prosesler": request.form.getlist(f"proses_{i}[]")
                })
            
            # Üretim özet kaydı (uretim tablosu)
            cursor.execute('''
    INSERT INTO uretim (is_emri_no, planlanan_sure_dk, durum, adet, aciklama, talep_id, musteri_id)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
''', (is_emri_no, total_planlanan_dakika, "bekliyor", adet, aciklama, talep_id, musteri_id))

            uretim_id = cursor.lastrowid
            
            # İş emri detaylarını yeni "is_emirleri" tablosuna ekle.
            # İlk satır ana iş emri, sonraki satırlar alt iş emri olarak numaralandırılır.
            for idx, row in enumerate(is_emirleri_data):
                current_is_emri_no = is_emri_no if idx == 0 else f"{is_emri_no}-{idx}"
                cursor.execute('''
                    INSERT INTO is_emirleri (is_emri_no, resim_no, adet, sure_dk, aciklama)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (current_is_emri_no, row["resim_no"], row["adet"], row["planlanan_dakika"], row["aciklama"]))
                is_emirleri_id = cursor.lastrowid
                # Seçilen prosesleri "prosesler" tablosuna ekle.
                for proses in row["prosesler"]:
                    cursor.execute('''
                        INSERT INTO prosesler (is_emri_id, proses)
                        VALUES (%s, %s)
                    ''', (is_emirleri_id, proses))
            
            conn.commit()
            return jsonify({"message": "İş emri başarıyla oluşturuldu!", "redirect": url_for('uretim')})
        
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            conn.rollback()
            return jsonify({"error": f"Bir hata oluştu: {str(e)}"}), 500
        finally:
            cursor.close()
            conn.close()
    
    # GET methodu: Talep ve Müşteri bilgilerini getir
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('''
        SELECT t.id, t.manes_numara, m.firma_unvani, t.musteri_no
        FROM talepler t
        JOIN musteriler m ON t.musteri_id = m.id
    ''')
    talepler = cursor.fetchall()
    cursor.execute('SELECT id, firma_unvani FROM musteriler')
    musteriler = cursor.fetchall()
    cursor.close()
    conn.close()
    
    is_emri_no = generate_is_emri_no()
    return render_template("uretim_olustur.html", talepler=talepler, musteriler=musteriler, is_emri_no=is_emri_no)



@app.route('/uretim')
@login_required()
def uretim():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM uretim")
    uretimler = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("uretim.html", uretimler=uretimler)

# İş emri detay sayfası: Uretim özet ve is_emirleri (detaylar) ile prosesleri getir.
@app.route('/uretim/<int:id>')
@login_required()
def uretim_detay(id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    # Uretim özetini getir
    cursor.execute("SELECT * FROM uretim WHERE id = %s", (id,))
    uretim = cursor.fetchone()
    if not uretim:
        cursor.close()
        conn.close()
        flash("İş emri bulunamadı!", "error")
        return redirect(url_for('uretim'))
    # is_emirleri (iş emri detayları) tablosundan ilgili kayıtları çek (aynı is_emri_no ile başlayanları getiriyoruz)
    ana_no = uretim['is_emri_no']
    cursor.execute("SELECT * FROM is_emirleri WHERE is_emri_no LIKE %s ORDER BY id ASC", (f"{ana_no}%",))
    is_emirleri_rows = cursor.fetchall()
    # Her is_emirleri kaydı için prosesleri çek
    alt_prosesler = {}
    for row in is_emirleri_rows:
        cursor.execute("SELECT proses FROM prosesler WHERE is_emri_id = %s", (row['id'],))
        alt_prosesler[row['id']] = [p['proses'] for p in cursor.fetchall()]
    cursor.close()
    conn.close()
    return render_template("uretim_detay.html", uretim=uretim, is_emirleri_rows=is_emirleri_rows, alt_prosesler=alt_prosesler)

# Üretim silme

@app.route('/uretim_sil/<int:id>', methods=['GET'])
@login_required()
def uretim_sil(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # 1. Üretim kaydını al
        cursor.execute('SELECT * FROM uretim WHERE id = %s', (id,))
        uretim = cursor.fetchone()

        if not uretim:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "İş emri bulunamadı!"})

        ana_no = uretim['is_emri_no']

        # 2. Alt iş emirlerinin ID'lerini al
        cursor.execute("SELECT id FROM is_emirleri WHERE is_emri_no LIKE %s", (f"{ana_no}%",))
        alt_ids = cursor.fetchall()

        # 3. Alt prosesleri sil
        for alt in alt_ids:
            cursor.execute("DELETE FROM prosesler WHERE is_emri_id = %s", (alt['id'],))

        # 4. Alt iş emirlerini sil
        cursor.execute("DELETE FROM is_emirleri WHERE is_emri_no LIKE %s", (f"{ana_no}%",))

        # 5. Üretim kaydını sil
        cursor.execute("DELETE FROM uretim WHERE id = %s", (id,))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": "İş emri ve bağlı alt kayıtlar başarıyla silindi!",
            "redirect": url_for('uretim')
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Hata oluştu: {str(e)}"
        })


# Üretim düzenleme sayfası
@app.route('/uretim_duzenle/<int:id>', methods=['GET', 'POST'])
@login_required()
def uretim_duzenle(id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == 'POST':
        try:
            # Verileri al
            is_emri_no = request.form.get('is_emri_no')
            total_planlanan_dakika = 0

            # 1. ÖNCE PROSESLERİ SİL (Yeni eklenen kısım)
            cursor.execute('''
                DELETE p FROM prosesler p
                JOIN is_emirleri i ON p.is_emri_id = i.id
                WHERE i.is_emri_no LIKE %s
            ''', (f"{is_emri_no}%",))

            # 2. SONRA İŞ EMİRLERİNİ SİL
            cursor.execute("DELETE FROM is_emirleri WHERE is_emri_no LIKE %s", (f"{is_emri_no}%",))

            # 3. YENİ KAYITLARI EKLE
            alt_resim_nolar = request.form.getlist('alt_resim_no[]')
            alt_adetler = request.form.getlist('alt_adet[]')
            alt_sureler = request.form.getlist('alt_tahmini_sure[]')
            alt_aciklamalar = request.form.getlist('alt_aciklama[]')

            for i in range(len(alt_resim_nolar)):
                try:
                    adet = int(alt_adetler[i]) if alt_adetler[i] else 0
                    sure_saat = float(alt_sureler[i].replace(",", ".")) if alt_sureler[i] else 0
                    sure_dk = int(sure_saat * 60)  # Saati dakikaya çevir
                    planlanan_dakika = adet * sure_dk
                    total_planlanan_dakika += planlanan_dakika

                    cursor.execute('''
                        INSERT INTO is_emirleri (is_emri_no, resim_no, adet, sure_dk, aciklama)
                        VALUES (%s, %s, %s, %s, %s)
                    ''', (
                        f"{is_emri_no}-{i}",
                        alt_resim_nolar[i],
                        adet,
                        planlanan_dakika,
                        alt_aciklamalar[i] if i < len(alt_aciklamalar) else ''
                    ))
                    is_emirleri_id = cursor.lastrowid

                    # Prosesleri ekle
                    prosesler = request.form.getlist(f'proses_{i}[]')
                    for proses in prosesler:
                        cursor.execute('''
                            INSERT INTO prosesler (is_emri_id, proses)
                            VALUES (%s, %s)
                        ''', (is_emirleri_id, proses))

                except Exception as e:
                    print(f"Alt iş emri kaydı hatası: {str(e)}")
                    continue

            # Üretim tablosunu güncelle
            cursor.execute('''
                UPDATE uretim 
                SET planlanan_sure_dk = %s
                WHERE id = %s
            ''', (total_planlanan_dakika, id))

            conn.commit()
            return jsonify({
                "message": "İş emri başarıyla güncellendi!",
                "redirect": url_for('uretim_detay', id=id)
            })

        except Exception as e:
            conn.rollback()
            return jsonify({"error": f"Bir hata oluştu: {str(e)}"}), 500
        finally:
            cursor.close()
            conn.close()

    # GET methodu için aynı kalıyor
    cursor.execute("SELECT * FROM uretim WHERE id = %s", (id,))
    uretim = cursor.fetchone()
    
    if not uretim:
        cursor.close()
        conn.close()
        return jsonify({"error": "Üretim kaydı bulunamadı!"}), 404

    ana_no = uretim['is_emri_no']
    cursor.execute("SELECT * FROM is_emirleri WHERE is_emri_no LIKE %s ORDER BY id ASC", (f"{ana_no}%",))
    is_emirleri_rows = cursor.fetchall()
    
    alt_prosesler = {}
    for row in is_emirleri_rows:
        cursor.execute("SELECT proses FROM prosesler WHERE is_emri_id = %s", (row['id'],))
        alt_prosesler[row['id']] = [p['proses'] for p in cursor.fetchall()]

    cursor.close()
    conn.close()

    return render_template(
        "uretim_duzenle.html",
        uretim=uretim,
        is_emirleri_rows=is_emirleri_rows,
        alt_prosesler=alt_prosesler
    )

# Üretim PDF çıktısı
@app.route('/uretim_pdf/<int:id>')
@login_required()
def uretim_pdf(id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # ✅ Üretim bilgisi + müşteri adı dahil çek
    cursor.execute("""
    SELECT u.*, t.musteri_no, t.manes_numara, m.firma_unvani AS musteri_adi
    FROM uretim u
    LEFT JOIN talepler t ON u.talep_id = t.id
    LEFT JOIN musteriler m ON t.musteri_id = m.id
    WHERE u.id = %s
""", (id,))

    uretim = cursor.fetchone()
    if not uretim:
        return "Üretim kaydı bulunamadı", 404

    # İş emirleri
    cursor.execute("SELECT * FROM is_emirleri WHERE is_emri_no LIKE %s ORDER BY id ASC", (f"{uretim['is_emri_no']}%",))
    is_emirleri_rows = cursor.fetchall()

    alt_prosesler = {}
    qr_dict = {}
    for row in is_emirleri_rows:
        cursor.execute("SELECT proses FROM prosesler WHERE is_emri_id = %s", (row['id'],))
        alt_prosesler[row['id']] = [p['proses'] for p in cursor.fetchall()]
        qr_dict[row['is_emri_no']] = generate_qr_base64(row['is_emri_no'])

    cursor.close()
    conn.close()

    logo_path = os.path.join(app.root_path, 'static', 'images', 'logo.png')
    logo_base64 = encode_logo_to_base64(logo_path)

    rendered_html = render_template(
        "uretim_pdf.html",
        uretim=uretim,
        is_emirleri_rows=is_emirleri_rows,
        alt_prosesler=alt_prosesler,
        qr_dict=qr_dict,
        logo_base64=logo_base64
    )

    config = pdfkit.configuration(wkhtmltopdf="/home/httpdrxq1/tmp/wkhtmltox/bin/wkhtmltopdf")
    options = {
        'orientation': 'Landscape',
        'page-size': 'A4',
        'margin-top': '10mm',
        'margin-bottom': '10mm',
        'margin-left': '10mm',
        'margin-right': '10mm',
        'encoding': "UTF-8",
        'enable-local-file-access': None
    }

    pdf = pdfkit.from_string(rendered_html, False, configuration=config, options=options)

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=uretim_{id}.pdf'
    return response
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Kullanıcıyı veritabanından al
        cursor.execute("SELECT * FROM kullanicilar WHERE username = %s", (username,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        # Kullanıcı varsa ve şifre doğruysa giriş başarılı
        if user and check_password_hash(user['password'], password):
            session['username'] = user['username']
            session['rol'] = user['rol']

            if user['rol'] == 'admin':
                return redirect('https://erp.manesltd.com.tr')
            else:
                return redirect('https://panel.manesltd.com.tr')
        else:
            flash("Kullanıcı adı veya şifre hatalı!", "error")
            return render_template("login.html")

    return render_template("login.html")
from werkzeug.security import generate_password_hash

@app.route('/kullanici_ekle', methods=['GET', 'POST'])
@login_required()  # Admin girişi yapmış biri kullanabilmeli
def kullanici_ekle():
    if session.get('rol') != 'admin':
        flash("Bu işlemi sadece admin kullanıcılar yapabilir!", "error")
        return redirect(url_for('home'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        rol = request.form['rol']

        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')


        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            # Aynı kullanıcı zaten var mı kontrol et
            cursor.execute("SELECT * FROM kullanicilar WHERE username = %s", (username,))
            if cursor.fetchone():
                flash("Bu kullanıcı zaten mevcut!", "warning")
                return redirect(url_for('kullanici_ekle'))

            cursor.execute('''
                INSERT INTO kullanicilar (username, password, rol)
                VALUES (%s, %s, %s)
            ''', (username, hashed_password, rol))

            conn.commit()
            cursor.close()
            conn.close()
            flash("Kullanıcı başarıyla eklendi ✅", "success")
            return redirect(url_for('kullanici_ekle'))

        except Exception as e:
            flash(f"Hata: {e}", "error")

    return render_template("kullanici_ekle.html")


@app.route('/panel/taleplerim')
@login_required()
def musteri_taleplerim():
    if session.get('rol') != 'musteri':
        return redirect(url_for('home'))  # admin yanlışlıkla girerse yönlendir

    kullanici_adi = session.get('username')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Giriş yapan kullanıcının müşteri_id'sini al
    cursor.execute('''
        SELECT m.id AS musteri_id, m.firma_unvani
        FROM kullanicilar k
        JOIN musteriler m ON k.username = m.email
        WHERE k.username = %s
    ''', (kullanici_adi,))
    musteri = cursor.fetchone()

    if not musteri:
        conn.close()
        return "Müşteri hesabı bulunamadı!", 404

    musteri_id = musteri['musteri_id']

    # Bu müşteriye ait talepler ve üretim durumları
    cursor.execute('''
        SELECT t.id AS talep_id, t.manes_numara, t.tarih, m.firma_unvani,
               u.durum AS uretim_durumu
        FROM talepler t
        LEFT JOIN uretim u ON t.id = u.talep_id
        JOIN musteriler m ON t.musteri_id = m.id
        WHERE t.musteri_id = %s
        ORDER BY t.tarih DESC
    ''', (musteri_id,))
    
    talepler = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('panel_taleplerim.html', talepler=talepler)
@app.route('/panel')
@login_required()
def panel_index():
    if session.get('rol') != 'musteri':
        return redirect(url_for('home'))

    kullanici_adi = session.get('username')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute('''
        SELECT m.id AS musteri_id, m.firma_unvani
        FROM kullanicilar k
        JOIN musteriler m ON k.username = m.email
        WHERE k.username = %s
    ''', (kullanici_adi,))
    musteri = cursor.fetchone()

    if not musteri:
        conn.close()
        return "Müşteri bulunamadı!", 404

    musteri_id = musteri['musteri_id']

    cursor.execute("SELECT COUNT(*) AS talep_sayisi FROM talepler WHERE musteri_id = %s", (musteri_id,))
    talep_sayisi = cursor.fetchone()['talep_sayisi']

    cursor.execute("SELECT COUNT(*) AS uretim_bekleyen FROM uretim WHERE musteri_id = %s AND durum = 'bekliyor'", (musteri_id,))
    uretim_bekleyen = cursor.fetchone()['uretim_bekleyen']

    cursor.execute("SELECT COUNT(*) AS teklif_onayli FROM teklifler WHERE musteri = %s AND durum = 'onaylandi'", (musteri['firma_unvani'],))
    teklif_onayli = cursor.fetchone()['teklif_onayli']

    cursor.close()
    conn.close()

    return render_template(
        'panel_index.html',
        musteri_adi=musteri['firma_unvani'],
        talep_sayisi=talep_sayisi,
        uretim_bekleyen=uretim_bekleyen,
        teklif_onayli=teklif_onayli
    )

@app.route('/logout')
def logout():
    session.clear()
    return redirect('https://www.manesltd.com.tr')


def generate_qr_base64(data):
    qr = qrcode.make(data)
    buf = BytesIO()
    qr.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('utf-8')

def encode_logo_to_base64(path):
    with open(path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode('utf-8')

# Uygulamayı çalıştır
# if __name__ == '__main__':
   #  app.run(debug=True, host='0.0.0.0', port=8080)
