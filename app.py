from flask import Flask, render_template, request, send_file, jsonify
import pandas as pd
import os
from werkzeug.utils import secure_filename
import traceback
import shutil

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 1000 * 1024 * 1024  # 1000MB max file size

# Create uploads folder if it doesn't exist
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

def cleanup_files():
    """Temizlik işlemleri için yardımcı fonksiyon"""
    try:
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            except Exception as e:
                print(f"Error deleting {file_path}: {e}")
    except Exception as e:
        print(f"Error during cleanup: {e}")

@app.route('/')
def login():
    return render_template('login.html')

@app.route('/index')
def index():
    return render_template('index.html')

@app.route('/template')
def template():
    return render_template('template.html')

@app.route('/kota')
def kota():
    return render_template('kota.html')

@app.route('/process_template', methods=['POST'])
def process_template():
    temp_filepath = None
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Dosya yüklenmedi'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'Dosya seçilmedi'}), 400

        # Get sepet limits from form
        try:
            row_limits = [
                int(request.form.get('sepet1', 0)),
                int(request.form.get('sepet2', 0)),
                int(request.form.get('sepet3', 0)),
                int(request.form.get('sepet4', 0))
            ]
        except ValueError:
            return jsonify({'error': 'Sepet sayıları geçerli sayılar olmalıdır'}), 400

        # Save uploaded file
        filename = secure_filename(file.filename)
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], f'temp_{filename}')
        file.save(temp_filepath)

        try:
            # Excel dosyasında sheet kontrolü
            try:
                df = pd.read_excel(temp_filepath, sheet_name='DepoCrossDock Rapor')
            except ValueError as e:
                return jsonify({'error': "Excel dosyasında 'DepoCrossDock Rapor' adlı bir sayfa (sheet) bulunamadı. Lütfen doğru dosyayı yükleyin."}), 400
            except Exception as e:
                return jsonify({'error': f'Excel dosyası okunamadı: {str(e)}'}), 400

            # Tek seferde filtrele
            mask = (df['Eleme Nedenleri'] == 'Sortlanmalı') & \
                   (~df['Sort Tanım'].str.contains('(ticaret|yardım|franchise|t99)', case=False, na=False))
            df = df[mask]
            
            # Groupby ve aggregate
            dfr = df.groupby('Sort Tanım', as_index=False)['Mağaza'].sum()
            
            # Sepet atama algoritması
            num_baskets = 4
            baskets = [{'satirlar': [], 'toplam_magaza': 0, 'count': 0} for _ in range(num_baskets)]
            df_to_process = dfr.sort_values(by='Mağaza', ascending=False).reset_index(drop=True)
            sepet_atamasi = []
            
            for idx, row in df_to_process.iterrows():
                uygun_sepetler = [i for i in range(num_baskets) if baskets[i]['count'] < row_limits[i]]
                if not uygun_sepetler:
                    continue
                en_uygun = min(uygun_sepetler, key=lambda i: baskets[i]['toplam_magaza'])
                baskets[en_uygun]['satirlar'].append(idx)
                baskets[en_uygun]['toplam_magaza'] += row['Mağaza']
                baskets[en_uygun]['count'] += 1
                sepet_atamasi.append(en_uygun + 1)

            df_processed = df_to_process.iloc[:len(sepet_atamasi)].copy()
            df_processed['Sepet'] = sepet_atamasi
            df_processed = df_processed.sort_values(by='Sepet', ascending=True)
            df_processed['Göz'] = range(1, len(df_processed) + 1)
            
            dfy = pd.merge(df, df_processed[['Sort Tanım', 'Sepet', 'Göz']], on='Sort Tanım', how='left')
            dfy = dfy[['KirikUrunMu', 'MerchYasGrupKod', 'MerchMarkaYasGrupKod', 'KlasmanGrupTanim', 'Klasman Ad', 'Ürün Klasman', 'Sort Tanım','Göz', 'Sepet']]
            dfy.insert(loc=0, column='TemplateID', value=None)
            dfy.insert(loc=dfy.columns.get_loc('KlasmanGrupTanim'), column='JelatinliMi', value='False')
            dfy.insert(loc=dfy.columns.get_loc('Sort Tanım'), column='Etiket', value=None)
            
            dfy = dfy.rename(columns={
                'KirikUrunMu': 'Temiz/Kırık',
                'Klasman Ad': 'UrunKlasmanTanim',
                'Ürün Klasman': 'UrunKlasmanKod',
                'Sort Tanım': 'Sort'
            })

            # Temiz/Kırık değerlerini düzelt
            dfy.loc[dfy['Temiz/Kırık'] == 'Bilgi Girilmemis', 'Temiz/Kırık'] = dfy.loc[dfy['Temiz/Kırık'] == 'Bilgi Girilmemis', 'Sort'].str.split('-').str[0]
            dfy.loc[dfy['Temiz/Kırık'] == 'E-TICARET KIRIK DEVİR', 'Temiz/Kırık'] = dfy.loc[dfy['Temiz/Kırık'] == 'E-TICARET KIRIK DEVİR', 'Sort'].str.split('-').str[0]
            
            replacements = {
                'Outlet Kırık Devir': 'Outlet Kırık',
                'Outlet': 'Outlet Kırık',
                'Inlet Kırık Devir': 'Temiz Devir & Inlet Kırık',
                'İnlet Kırık Devir': 'Temiz Devir & Inlet Kırık',
                'Temiz Devir': 'Temiz Devir & Inlet Kırık',
                'ASORTİLİ TEMİZ DEVİR': 'Temiz Devir & Inlet Kırık',
                'İnlet': 'Temiz Devir & Inlet Kırık',
                'Inlet': 'Temiz Devir & Inlet Kırık'
            }
            
            for old, new in replacements.items():
                dfy['Temiz/Kırık'] = dfy['Temiz/Kırık'].replace(old, new)
            
            dfy.drop_duplicates(inplace=True)
            
            # Save processed file
            output_path = os.path.join(app.config['UPLOAD_FOLDER'], 'final_template.xlsx')
            dfy.to_excel(output_path, index=False)
            
            return jsonify({'status': 'success'}), 200
            
        except Exception as e:
            error_msg = f'Excel işleme hatası: {str(e)}\n{traceback.format_exc()}'
            print(error_msg)  # Sunucu loglarına yazdır
            return jsonify({'error': 'Excel dosyası işlenirken bir hata oluştu. Lütfen dosyanızı kontrol edin.'}), 500
            
    except Exception as e:
        error_msg = f'Beklenmeyen hata: {str(e)}\n{traceback.format_exc()}'
        print(error_msg)  # Sunucu loglarına yazdır
        return jsonify({'error': 'Beklenmeyen bir hata oluştu. Lütfen daha sonra tekrar deneyin.'}), 500
        
    finally:
        # Geçici dosyaları temizle
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except:
                pass

@app.route('/download_template')
def download_template():
    try:
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], 'final_template.xlsx')
        if not os.path.exists(output_path):
            return jsonify({'error': 'Template dosyası bulunamadı'}), 404
        return send_file(output_path, as_attachment=True)
    except Exception as e:
        return jsonify({'error': 'Dosya indirme hatası'}), 500

if __name__ == '__main__':
    app.run(debug=True) 
