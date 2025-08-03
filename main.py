# main.py の最終版コード（これを丸ごと貼り付けてください）

from flask import Flask, render_template, request, jsonify
import os
import requests
import uuid
from datetime import datetime, timezone, timedelta
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

app = Flask(__name__)

# --- 設定値 ---
NPK_FOLDER_ID = os.environ.get('NPK_FOLDER_ID')
HABITAT_IMAGE_FOLDER_ID = os.environ.get('HABITAT_IMAGE_FOLDER_ID')
KINTONE_UUID_FIELD_CODE = 'uuid'
KINTONE_STATUS_FIELD_CODE = 'ocr_status'
KINTONE_NPK_TYPE_FIELD_CODE = 'npk_test_type'
# --- 設定値ここまで ---


def find_or_create_folder(drive_service, parent_folder_id, folder_name):
    query = f"'{parent_folder_id}' in parents and name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    response = drive_service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name)',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True).execute()
    folders = response.get('files', [])
    if folders:
        print(
            f"Folder '{folder_name}' already exists. Using ID: {folders[0].get('id')}"
        )
        return folders[0].get('id')
    else:
        print(f"Folder '{folder_name}' not found. Creating new folder.")
        folder_metadata = {
            'name': folder_name,
            'parents': [parent_folder_id],
            'mimeType': 'application/vnd.google-apps.folder'
        }
        new_folder = drive_service.files().create(
            body=folder_metadata, fields='id',
            supportsAllDrives=True).execute()
        print(
            f"Created new folder '{folder_name}'. ID: {new_folder.get('id')}")
        return new_folder.get('id')


def upload_file_to_google_drive(file_storage, filename, folder_id):
    try:
        creds_json_str = os.environ.get('GOOGLE_CREDENTIALS_JSON')
        if not creds_json_str:
            return False, "サーバーにGoogle認証情報(Secret)が設定されていません。"
        if not folder_id:
            return False, "アップロード先のGoogle Drive フォルダIDが設定されていません。"
        creds_info = json.loads(creds_json_str)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=['https://www.googleapis.com/auth/drive'])
        drive_service = build('drive', 'v3', credentials=credentials)
        file_metadata = {'name': filename, 'parents': [folder_id]}
        file_stream = io.BytesIO(file_storage.read())
        media = MediaIoBaseUpload(file_stream,
                                  mimetype=file_storage.mimetype,
                                  resumable=True)
        drive_service.files().create(body=file_metadata,
                                     media_body=media,
                                     fields='id',
                                     supportsAllDrives=True).execute()
        print(
            f"Successfully uploaded {filename} to Google Drive folder {folder_id}."
        )
        return True, "Google Driveへのアップロードに成功しました。"
    except Exception as e:
        print(f"Google Drive Upload Error: {e}")
        return False, f"Google Driveへのアップロード中にエラーが発生しました: {e}"


# --- 表示・認証関連のルート (変更なし) ---
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/clock-in')
def clock_in():
    return render_template('clock_in.html')


@app.route('/record-attendance', methods=['POST'])
def record_attendance():
    # この関数の中身は変更ありません
    kintone_domain = os.environ.get('KINTONE_DOMAIN')
    user_master_app_id = os.environ.get('KINTONE_USER_MASTER_APP_ID')
    user_master_api_token = os.environ.get('KINTONE_USER_MASTER_API_TOKEN')
    attendance_app_id = os.environ.get('KINTONE_ATTENDANCE_APP_ID')
    attendance_api_token = os.environ.get('KINTONE_ATTENDANCE_API_TOKEN')
    if not all([
            kintone_domain, user_master_app_id, user_master_api_token,
            attendance_app_id, attendance_api_token
    ]):
        return jsonify({
            'success': False,
            'message': 'サーバーに必要なKintone設定がありません。'
        }), 500
    data = request.get_json()
    worker_id = data.get('worker_id')
    if not worker_id:
        return jsonify({'success': False, 'message': '作業者IDがありません。'}), 400
    try:
        lookup_url = f"https://{kintone_domain}/k/v1/records.json"
        lookup_headers = {
            'X-Cybozu-API-Token': user_master_api_token
        }
        lookup_params = {
            'app': user_master_app_id,
            'query': f'userid_master = "{worker_id}"',
            'fields': ['username_master']
        }
        lookup_response = requests.get(lookup_url,
                                       headers=lookup_headers,
                                       params=lookup_params)
        lookup_response.raise_for_status()
        records = lookup_response.json().get('records', [])
        if not records:
            return jsonify({
                'success': False,
                'message': f'ID「{worker_id}」の作業者が見つかりません。'
            }), 404
        worker_name = records[0].get('username_master', {}).get('value')
    except requests.exceptions.RequestException as e:
        print(
            f"User Master Lookup Error: {e.response.text if e.response else e}"
        )
        return jsonify({
            'success': False,
            'message': 'ユーザーマスタの検索に失敗しました。'
        }), 500
    try:
        jst = timezone(timedelta(hours=+9), 'JST')
        now_jst = datetime.now(jst).isoformat()
        latitude = data.get('latitude')
        longitude = data.get('longitude')
        accuracy = data.get('accuracy')
        record_payload_data = {
            'worker_id': {
                'value': worker_id
            },
            'worker_name': {
                'value': worker_name
            },
            'clock_in_time': {
                'value': now_jst
            },
            'latitude': {
                'value': latitude
            },
            'longitude': {
                'value': longitude
            },
            'location_accuracy': {
                'value': accuracy
            },
            'map_link': {
                'value':
                f"https://www.google.com/maps?q={latitude},{longitude}"
                if latitude and longitude else ""
            }
        }
        record_to_send = {
            k: v
            for k, v in record_payload_data.items()
            if v.get('value') is not None
        }
        record_payload = {'app': attendance_app_id, 'record': record_to_send}
        record_url = f"https://{kintone_domain}/k/v1/record.json"
        record_headers = {
            'X-Cybozu-API-Token': attendance_api_token,
            'Content-Type': 'application/json'
        }
        record_response = requests.post(record_url,
                                        json=record_payload,
                                        headers=record_headers)
        record_response.raise_for_status()
        return jsonify({
            'success': True,
            'message': '出勤を記録しました。',
            'worker_name': worker_name
        })
    except requests.exceptions.RequestException as e:
        print(
            f"Attendance Record Error: {e.response.text if e.response else e}")
        return jsonify({
            'success': False,
            'message': '出勤記録に失敗しました。'
        }), 500


# --- NPK画像（OCR対象）のアップロード処理 ---
@app.route('/submit', methods=['POST'])
def submit():
    kintone_domain = os.environ.get('KINTONE_DOMAIN')
    kintone_api_token = os.environ.get('KINTONE_API_TOKEN')
    if not all([kintone_domain, kintone_api_token]):
        return jsonify({
            'success': False,
            'message': 'サーバーにKintone設定がありません。'
        }), 500

    image_file = request.files.get('photo_npk_test_type')
    if not image_file or image_file.filename == '':
        return jsonify({'success': False, 'message': '画像が選択されていません。'}), 400

    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
    # ★★★ NPK画像のファイル名生成ロジックを変更 ★★★
    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
    record_uuid = str(uuid.uuid4())
    tray_id = request.form.get('treiID')
    if not tray_id:
        return jsonify({'success': False, 'message': 'トレイIDが入力されていません。'}), 400

    jst = timezone(timedelta(hours=+9), 'JST')
    timestamp = datetime.now(jst).strftime('%Y-%m-%dT%H-%M-%S')
    _, original_extension = os.path.splitext(image_file.filename)

    # 新しいファイル名の形式: 「トレイID_日時_連携ID.拡張子」
    drive_filename = f"{tray_id}_{timestamp}_{record_uuid}{original_extension}"

    # Google Driveへアップロード
    success, message = upload_file_to_google_drive(image_file, drive_filename,
                                                   NPK_FOLDER_ID)
    if not success:
        return jsonify({'success': False, 'message': message}), 500

    # 2. Kintoneへ先行登録 (この部分は従来通り)
    form_data = request.form
    record_payload = {
        KINTONE_UUID_FIELD_CODE: {
            'value': record_uuid
        },
        KINTONE_STATUS_FIELD_CODE: {
            'value': 'OCR処理中'
        },
        KINTONE_NPK_TYPE_FIELD_CODE: {
            'value': '土壌検査'
        },
        'placeID': {
            'value': form_data.get('placeID')
        },
        'houseID': {
            'value': form_data.get('houseID')
        },
        'treiID': {
            'value': form_data.get('treiID')
        },
        'username': {
            'value': form_data.get('username')
        },
        'worker_id': {
            'value': form_data.get('worker_id')
        },
        'memo': {
            'value': form_data.get('memo')
        }
    }
    record_to_send = {
        k: v
        for k, v in record_payload.items() if v.get('value')
    }
    kintone_payload = {'app': 129, 'record': record_to_send}
    record_url = f"https://{kintone_domain}/k/v1/record.json"
    record_headers = {
        'X-Cybozu-API-Token': kintone_api_token,
        'Content-Type': 'application/json'
    }
    try:
        response = requests.post(record_url,
                                 json=kintone_payload,
                                 headers=record_headers)
        response.raise_for_status()
        return jsonify({
            'success': True,
            'message': 'アップロードとKintoneへの登録を開始しました。'
        })
    except requests.exceptions.RequestException as e:
        print(f"Kintone Error: {e.response.text if e.response else e}")
        return jsonify({
            'success': False,
            'message': 'Kintoneへの先行登録に失敗しました。'
        }), 500


# --- 生息画像のアップロード処理 ---
@app.route('/upload_habitat_image', methods=['POST'])
def upload_habitat_image():
    image_file = request.files.get('habitat_image')
    tray_id = request.form.get('treiID')

    if not image_file or image_file.filename == '':
        return jsonify({'success': False, 'message': '画像が選択されていません。'}), 400
    if not tray_id:
        return jsonify({'success': False, 'message': 'トレイIDが入力されていません。'}), 400

    try:
        creds_json_str = os.environ.get('GOOGLE_CREDENTIALS_JSON')
        creds_info = json.loads(creds_json_str)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=['https://www.googleapis.com/auth/drive'])
        drive_service = build('drive', 'v3', credentials=credentials)
        target_folder_id = find_or_create_folder(drive_service,
                                                 HABITAT_IMAGE_FOLDER_ID,
                                                 tray_id)
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Google Driveのフォルダ操作中にエラー: {e}'
        }), 500

    jst = timezone(timedelta(hours=+9), 'JST')
    timestamp = datetime.now(jst).strftime('%Y-%m-%dT%H-%M-%S')
    _, original_extension = os.path.splitext(image_file.filename)
    drive_filename = f"{tray_id}_{timestamp}{original_extension}"
    success, message = upload_file_to_google_drive(image_file, drive_filename,
                                                   target_folder_id)
    if not success: return jsonify({'success': False, 'message': message}), 500

    return jsonify({'success': True, 'message': '生息画像のアップロードが完了しました。'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
