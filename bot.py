import os
import logging
import re
import time
from datetime import datetime
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import telebot
from telebot import types
from openai import OpenAI
import base64
from PIL import Image, ImageDraw, ImageFont
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import psycopg2
from psycopg2.extras import RealDictCursor
import uuid
import json

# Tải biến môi trường từ file .env
load_dotenv()

# Cấu hình logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# API Keys
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
YESCALE_API_KEY = os.getenv('YESCALE_API_KEY')
AIROUTER_API_KEY = os.getenv('AIROUTER_API_KEY', 'sk-9lA2bexmmJOs5hU-nkc8gg')

# Thêm vào phần biến môi trường
SUPABASE_DB_HOST = os.getenv('SUPABASE_DB_HOST')
SUPABASE_DB_PORT = os.getenv('SUPABASE_DB_PORT')
SUPABASE_DB_NAME = os.getenv('SUPABASE_DB_NAME')
SUPABASE_DB_USER = os.getenv('SUPABASE_DB_USER')
SUPABASE_DB_PASSWORD = os.getenv('SUPABASE_DB_PASSWORD')

# Thêm vào phần biến môi trường cho các phương thức kết nối khác
SUPABASE_POOLER_HOST = os.getenv('SUPABASE_POOLER_HOST', 'aws-0-ap-southeast-1.pooler.supabase.com')
SUPABASE_POOLER_PORT = os.getenv('SUPABASE_POOLER_PORT', '6543')
SUPABASE_POOLER_USER = os.getenv('SUPABASE_POOLER_USER', 'postgres.nscsnynjuzebwtmicukk')

# Khởi tạo bot
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Lưu trữ trạng thái người dùng
user_states = {}

# Enum trạng thái
WAITING_FOR_BIRTH_DATE = 1
WAITING_FOR_BIRTH_TIME = 2

# Tạo thư mục assets nếu chưa tồn tại
if not os.path.exists('assets'):
    os.makedirs('assets')

# Dictionary lưu trữ số lượng lá số đã tạo cho mỗi người dùng
user_chart_counts = {}

# Khởi tạo OpenAI client với AIRouter
openai_client = OpenAI(
    base_url="https://api.airouter.io",
    api_key=AIROUTER_API_KEY
)

# Thêm biến toàn cục để theo dõi thống kê
bot_stats = {
    'start_time': datetime.now(),
    'charts_created': 0,
    'charts_reused': 0,
    'analyses_performed': 0,
    'errors': 0
}

# Hàm gửi thống kê cho admin
def send_stats_to_admin(admin_id):
    """
    Gửi thống kê sử dụng bot cho admin
    
    Args:
        admin_id (int): ID của admin
    """
    try:
        # Tính thời gian hoạt động
        uptime = datetime.now() - bot_stats['start_time']
        days, seconds = uptime.days, uptime.seconds
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        
        # Định dạng thời gian hoạt động
        uptime_str = f"{days} ngày, {hours} giờ, {minutes} phút, {seconds} giây"
        
        # Tạo thông báo thống kê
        stats_message = (
            "📊 *THỐNG KÊ BOT TỬ VI*\n\n"
            f"⏱ *Thời gian hoạt động*: {uptime_str}\n"
            f"📈 *Lá số đã tạo*: {bot_stats['charts_created']}\n"
            f"♻️ *Lá số tái sử dụng*: {bot_stats['charts_reused']}\n"
            f"🔮 *Phân tích đã thực hiện*: {bot_stats['analyses_performed']}\n"
            f"❌ *Lỗi đã gặp*: {bot_stats['errors']}\n\n"
            f"🖥 *Thời điểm khởi động*: {bot_stats['start_time'].strftime('%d/%m/%Y %H:%M:%S')}\n"
            f"🕒 *Thời điểm hiện tại*: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
        )
        
        # Gửi thông báo cho admin
        bot.send_message(
            admin_id,
            stats_message,
            parse_mode='Markdown'
        )
        
        logger.info(f"Đã gửi thống kê cho admin {admin_id}")
        
    except Exception as e:
        logger.error(f"Lỗi khi gửi thống kê cho admin: {e}")

# Phương án dự phòng nếu vẫn không kết nối được
def create_temporary_database():
    """Tạo cơ sở dữ liệu tạm thời bằng SQLite nếu không kết nối được Supabase"""
    try:
        import sqlite3
        logger.info("Tạo cơ sở dữ liệu SQLite tạm thời")
        
        # Tạo thư mục data nếu chưa tồn tại
        if not os.path.exists('data'):
            os.makedirs('data')
        
        # Kết nối đến database SQLite
        conn = sqlite3.connect('data/tuvi_temp.db')
        cursor = conn.cursor()
        
        # Tạo bảng users
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                first_name TEXT,
                last_name TEXT,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Tạo bảng charts
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS charts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                day INTEGER NOT NULL,
                month INTEGER NOT NULL,
                year INTEGER NOT NULL,
                birth_time TEXT NOT NULL,
                gender TEXT NOT NULL,
                chart_image TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            )
        ''')
        
        conn.commit()
        logger.info("Đã khởi tạo cơ sở dữ liệu SQLite thành công")
        return conn
    except Exception as e:
        logger.error(f"Lỗi khi tạo cơ sở dữ liệu tạm thời: {e}")
        return None

# Hàm kết nối đến Supabase với nhiều phương thức thử khác nhau
def get_db_connection():
    # Danh sách các cấu hình kết nối để thử
    connection_configs = [
        # Kết nối trực tiếp (Direct connection)
        {
            'host': SUPABASE_DB_HOST,
            'port': SUPABASE_DB_PORT,
            'database': SUPABASE_DB_NAME,
            'user': SUPABASE_DB_USER,
            'password': SUPABASE_DB_PASSWORD
        },
        # Kết nối qua Transaction pooler
        {
            'host': SUPABASE_POOLER_HOST,
            'port': SUPABASE_POOLER_PORT,
            'database': SUPABASE_DB_NAME,
            'user': SUPABASE_POOLER_USER,
            'password': SUPABASE_DB_PASSWORD
        },
        # Kết nối qua Session pooler
        {
            'host': SUPABASE_POOLER_HOST,
            'port': '5432',
            'database': SUPABASE_DB_NAME,
            'user': SUPABASE_POOLER_USER,
            'password': SUPABASE_DB_PASSWORD
        }
    ]
    
    # Thử từng cấu hình kết nối cho đến khi thành công
    last_error = None
    for config in connection_configs:
        try:
            logger.info(f"Đang thử kết nối đến cơ sở dữ liệu với host: {config['host']} và port: {config['port']}")
            conn = psycopg2.connect(
                host=config['host'],
                port=config['port'],
                database=config['database'],
                user=config['user'],
                password=config['password'],
                connect_timeout=10  # Thêm timeout để không đợi quá lâu
            )
            conn.autocommit = True
            logger.info(f"Kết nối thành công đến cơ sở dữ liệu với host: {config['host']}")
            return conn
        except Exception as e:
            last_error = e
            logger.warning(f"Không thể kết nối đến cơ sở dữ liệu với cấu hình: {config['host']}:{config['port']} - Lỗi: {e}")
    
    # Nếu tất cả đều thất bại
    logger.error(f"Tất cả các phương thức kết nối đều thất bại. Lỗi cuối cùng: {last_error}")
    return None

# Hàm khởi tạo các bảng trong database
def init_database():
    conn = get_db_connection()
    if not conn:
        logger.error("Không thể khởi tạo cơ sở dữ liệu")
        return
    
    try:
        cursor = conn.cursor()
        
        # Tạo bảng users
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                username VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tạo bảng charts (lá số tử vi)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS charts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(telegram_id),
                day INTEGER NOT NULL,
                month INTEGER NOT NULL,
                year INTEGER NOT NULL,
                birth_time VARCHAR(50) NOT NULL,
                gender VARCHAR(10) NOT NULL,
                chart_image TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        logger.info("Đã khởi tạo cơ sở dữ liệu thành công")
    except Exception as e:
        logger.error(f"Lỗi khi khởi tạo cơ sở dữ liệu: {e}")
    finally:
        cursor.close()
        conn.close()

@bot.message_handler(commands=['start'])
def start(message):
    """Bắt đầu hội thoại và yêu cầu ngày tháng năm sinh."""
    chat_id = message.chat.id
    
    # Lưu thông tin người dùng vào cơ sở dữ liệu
    save_user(message.from_user)
    
    # Lời chào thân thiện hơn
    welcome_message = (
        "🌟 *Chào mừng bạn đến với Bot Tử Vi!* 🌟\n\n"
        "Bot sẽ giúp bạn lập và phân tích lá số tử vi dựa trên thông tin ngày sinh của bạn.\n\n"
        "👉 Vui lòng nhập ngày tháng năm sinh của bạn theo định dạng DD/MM/YYYY (ví dụ: 15/08/1990):"
    )
    
    bot.send_message(
        chat_id,
        welcome_message,
        parse_mode='Markdown'
    )
    user_states[chat_id] = WAITING_FOR_BIRTH_DATE

@bot.message_handler(func=lambda message: user_states.get(message.chat.id) == WAITING_FOR_BIRTH_DATE)
def get_birth_date(message):
    """Nhận ngày tháng năm sinh và yêu cầu giờ sinh."""
    chat_id = message.chat.id
    birth_date = message.text.strip()
    
    # Kiểm tra định dạng ngày tháng
    pattern = r'^(\d{1,2})/(\d{1,2})/(\d{4})$'
    match = re.match(pattern, birth_date)
    
    if not match:
        bot.send_message(
            chat_id,
            "⚠️ *Định dạng ngày tháng không đúng*\n\n"
            "Vui lòng nhập theo định dạng DD/MM/YYYY\n"
            "Ví dụ: 15/08/1990 hoặc 5/4/1985",
            parse_mode='Markdown'
        )
        return
    
    day, month, year = match.groups()
    day, month, year = int(day), int(month), int(year)
    
    # Kiểm tra tính hợp lệ của ngày tháng
    if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100):
        bot.send_message(
            chat_id,
            "⚠️ *Ngày tháng không hợp lệ*\n\n"
            "Vui lòng kiểm tra lại ngày, tháng, năm sinh của bạn và nhập lại.",
            parse_mode='Markdown'
        )
        return
    
    # Lưu thông tin vào user_states
    user_states[chat_id] = {
        'state': WAITING_FOR_BIRTH_TIME,
        'day': day,
        'month': month,
        'year': year
    }
    
    # Tạo bàn phím inline để chọn giờ sinh với emoji
    markup = types.InlineKeyboardMarkup(row_width=3)
    
    # Hàng 1
    btn_ty = types.InlineKeyboardButton("🕛 Tý (23h-1h)", callback_data="ty")
    btn_suu = types.InlineKeyboardButton("🕐 Sửu (1h-3h)", callback_data="suu")
    btn_dan = types.InlineKeyboardButton("🕒 Dần (3h-5h)", callback_data="dan")
    markup.add(btn_ty, btn_suu, btn_dan)
    
    # Hàng 2
    btn_mao = types.InlineKeyboardButton("🕔 Mão (5h-7h)", callback_data="mao")
    btn_thin = types.InlineKeyboardButton("🕖 Thìn (7h-9h)", callback_data="thin")
    btn_ty_hora = types.InlineKeyboardButton("🕘 Tỵ (9h-11h)", callback_data="ty_hora")
    markup.add(btn_mao, btn_thin, btn_ty_hora)
    
    # Hàng 3
    btn_ngo = types.InlineKeyboardButton("🕚 Ngọ (11h-13h)", callback_data="ngo")
    btn_mui = types.InlineKeyboardButton("🕜 Mùi (13h-15h)", callback_data="mui")
    btn_than = types.InlineKeyboardButton("🕞 Thân (15h-17h)", callback_data="than")
    markup.add(btn_ngo, btn_mui, btn_than)
    
    # Hàng 4
    btn_dau = types.InlineKeyboardButton("🕠 Dậu (17h-19h)", callback_data="dau")
    btn_tuat = types.InlineKeyboardButton("🕢 Tuất (19h-21h)", callback_data="tuat")
    btn_hoi = types.InlineKeyboardButton("🕤 Hợi (21h-23h)", callback_data="hoi")
    markup.add(btn_dau, btn_tuat, btn_hoi)
    
    # Hàng 5
    btn_unknown = types.InlineKeyboardButton("❓ Không rõ giờ sinh", callback_data="unknown")
    markup.add(btn_unknown)
    
    bot.send_message(
        chat_id, 
        f"🕐 *Chọn giờ sinh của bạn:*\n\nNgày sinh: {day}/{month}/{year}", 
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda call: True)
def get_birth_time(call):
    """Nhận giờ sinh và hỏi giới tính."""
    chat_id = call.message.chat.id
    
    if isinstance(user_states.get(chat_id), dict) and user_states[chat_id].get('state') == WAITING_FOR_BIRTH_TIME:
        time_mapping = {
            "ty": "Tý", "suu": "Sửu", "dan": "Dần", "mao": "Mão", 
            "thin": "Thìn", "ty_hora": "Tỵ", "ngo": "Ngọ", "mui": "Mùi", 
            "than": "Thân", "dau": "Dậu", "tuat": "Tuất", "hoi": "Hợi",
            "unknown": "Không rõ"
        }
        
        # Nếu callback data là "analyze" hoặc "cancel_analysis", xử lý phân tích
        if call.data == "analyze":
            # Xử lý phân tích lá số tử vi
            process_analysis(chat_id)
            return
        elif call.data == "cancel_analysis":
            bot.send_message(
                chat_id, 
                "✅ Đã hủy phân tích. Bạn có thể gõ /start để lập lá số tử vi mới.",
                parse_mode='Markdown'
            )
            del user_states[chat_id]
            return
        elif call.data == "male":
            # Người dùng chọn giới tính Nam
            user_states[chat_id]['gender'] = "Nam"
            process_tuvi_chart(chat_id)
            return
        elif call.data == "female":
            # Người dùng chọn giới tính Nữ
            user_states[chat_id]['gender'] = "Nữ"
            process_tuvi_chart(chat_id)
            return
        
        birth_time = time_mapping.get(call.data, "Không rõ")
        user_states[chat_id]['birth_time'] = birth_time
        
        # Thông báo đã chọn giờ sinh
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=call.message.message_id,
                text=f"✅ Bạn đã chọn giờ sinh: *{birth_time}*",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.warning(f"Không thể cập nhật tin nhắn: {e}")
            # Gửi tin nhắn mới nếu không thể cập nhật tin nhắn cũ
            try:
                bot.send_message(
                    chat_id,
                    f"✅ Bạn đã chọn giờ sinh: *{birth_time}*",
                    parse_mode='Markdown'
                )
            except Exception as e2:
                logger.error(f"Không thể gửi tin nhắn xác nhận giờ sinh: {e2}")
        
        # Tạo bàn phím inline để chọn giới tính
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_male = types.InlineKeyboardButton("👨 Nam", callback_data="male")
        btn_female = types.InlineKeyboardButton("👩 Nữ", callback_data="female")
        markup.add(btn_male, btn_female)
        
        bot.send_message(
            chat_id,
            "👫 *Vui lòng chọn giới tính:*",
            reply_markup=markup,
            parse_mode='Markdown'
        )
    else:
        # Xử lý trường hợp callback không hợp lệ
        try:
            # Kiểm tra xem callback có phải là từ một nút phân tích hoặc xem lá số không
            if call.data.startswith("analyze_chart_") or call.data.startswith("view_chart_") or call.data.startswith("detail_"):
                # Để các hàm xử lý callback khác xử lý
                return
            
            # Thông báo lỗi nếu không phải callback hợp lệ
            bot.answer_callback_query(
                call.id,
                "⚠️ Yêu cầu không hợp lệ hoặc đã hết hạn. Vui lòng thử lại.",
                show_alert=True
            )
        except Exception as e:
            logger.error(f"Lỗi khi xử lý callback không hợp lệ: {e}")

def process_tuvi_chart(chat_id):
    """Xử lý lá số tử vi."""
    # Gửi thông báo đang xử lý
    processing_msg = bot.send_message(
        chat_id, 
        "⏳ *Đang lập lá số tử vi...*\n\nVui lòng đợi trong giây lát, quá trình này có thể mất 30-60 giây.",
        parse_mode='Markdown'
    )
    
    try:
        # Lấy thông tin từ trạng thái người dùng
        user_data = user_states[chat_id]
        day = user_data['day']
        month = user_data['month']
        year = user_data['year']
        birth_time = user_data['birth_time']
        gender = user_data['gender']
        
        # Lấy lá số tử vi và truyền thêm user_id
        result_path, is_existing = get_tuvi_chart(day, month, year, birth_time, gender, chat_id, user_data)
        
        # Xóa thông báo đang xử lý
        try:
            bot.delete_message(chat_id, processing_msg.message_id)
        except Exception as e:
            logger.warning(f"Không thể xóa tin nhắn đang xử lý: {e}")
        
        # Lưu đường dẫn kết quả vào trạng thái người dùng
        if result_path.endswith('.html'):
            user_states[chat_id]['chart_html_path'] = result_path
        else:
            user_states[chat_id]['chart_image_path'] = result_path
        
        # Chuẩn bị caption với thông tin chi tiết
        caption = f"✨ *Lá số tử vi của bạn*\n\n• Ngày sinh: {day}/{month}/{year}\n• Giờ sinh: {birth_time}\n• Giới tính: {gender}"
        
        # Thêm thông báo nếu là lá số đã tồn tại
        if is_existing:
            caption += "\n\n📝 *Ghi chú: Lá số này đã tồn tại trong hệ thống và được tái sử dụng.*"
        
        # Gửi kết quả cho người dùng
        if result_path.endswith('.jpg') or result_path.endswith('.png'):
            # Nếu là ảnh, gửi trực tiếp
            with open(result_path, 'rb') as photo:
                bot.send_photo(
                    chat_id,
                    photo,
                    caption=caption,
                    reply_markup=types.InlineKeyboardMarkup().add(
                        types.InlineKeyboardButton("🔮 Phân tích lá số", callback_data="analyze"),
                        types.InlineKeyboardButton("❌ Hủy", callback_data="cancel_analysis")
                    ),
                    parse_mode='Markdown'
                )
        else:
            # Nếu là HTML, chuyển đổi thành ảnh
            screenshot_path = html_to_image(result_path, chat_id)
            with open(screenshot_path, 'rb') as photo:
                bot.send_photo(
                    chat_id,
                    photo,
                    caption=caption,
                    reply_markup=types.InlineKeyboardMarkup().add(
                        types.InlineKeyboardButton("🔮 Phân tích lá số", callback_data="analyze"),
                        types.InlineKeyboardButton("❌ Hủy", callback_data="cancel_analysis")
                    ),
                    parse_mode='Markdown'
                )
            # Lưu đường dẫn ảnh
            user_states[chat_id]['chart_image_path'] = screenshot_path
        
    except Exception as e:
        logger.error(f"Lỗi khi xử lý lá số tử vi: {e}")
        # Xóa thông báo đang xử lý
        try:
            bot.delete_message(chat_id, processing_msg.message_id)
        except:
            pass
            
        bot.send_message(
            chat_id,
            "❌ *Đã xảy ra lỗi khi xử lý lá số tử vi*\n\nVui lòng thử lại sau.",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("🔄 Thử lại", callback_data="/start")
            ),
            parse_mode='Markdown'
        )
        # Xóa trạng thái người dùng
        del user_states[chat_id]

def send_progress_update(chat_id, message_id, progress_text, progress_percent=None):
    """
    Cập nhật thông báo tiến trình xử lý
    
    Args:
        chat_id (int): ID của chat
        message_id (int): ID của tin nhắn cần cập nhật
        progress_text (str): Nội dung thông báo tiến trình
        progress_percent (int, optional): Phần trăm tiến trình (0-100)
    """
    try:
        # Tạo thanh tiến trình nếu có phần trăm
        progress_bar = ""
        if progress_percent is not None:
            # Đảm bảo giá trị nằm trong khoảng 0-100
            progress_percent = max(0, min(100, progress_percent))
            
            # Tạo thanh tiến trình với emoji
            filled = int(progress_percent / 10)
            empty = 10 - filled
            progress_bar = f"\n[{'🟩' * filled}{'⬜' * empty}] {progress_percent}%\n"
        
        # Cập nhật tin nhắn
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"⏳ *Đang lập lá số tử vi...*\n\n{progress_text}{progress_bar}",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.warning(f"Không thể cập nhật thông báo tiến trình: {e}")

def get_tuvi_chart(day, month, year, birth_time, gender, user_id, user_data):
    """
    Lấy lá số tử vi dựa trên thông tin ngày sinh.
    Kiểm tra xem lá số đã tồn tại chưa, nếu có thì tái sử dụng.
    """
    try:
        # Kiểm tra xem lá số đã tồn tại chưa
        chart_exists, existing_chart_path, chart_id = check_existing_chart(
            user_id, day, month, year, birth_time, gender
        )
        
        if chart_exists and existing_chart_path:
            logger.info(f"Tái sử dụng lá số đã tồn tại cho user {user_id}: {existing_chart_path}")
            # Cập nhật thống kê
            bot_stats['charts_reused'] += 1
            return existing_chart_path, True  # True để đánh dấu đây là lá số tái sử dụng
        
        # Nếu không tìm thấy lá số tồn tại, tạo mới
        logger.info(f"Tạo lá số mới cho user {user_id} với thông tin: {day}/{month}/{year}, {birth_time}, {gender}")
        
        # Thông báo đang xử lý
        logger.info(f"Đang lấy lá số tử vi cho {day}/{month}/{year}, giờ {birth_time}, giới tính {gender}")
        
        # Chuyển đổi giờ sinh theo định dạng giờ (lấy giá trị trung bình của khoảng giờ)
        hour_mapping = {
            "Tý": "00", "Sửu": "02", "Dần": "04", "Mão": "06", 
            "Thìn": "08", "Tỵ": "10", "Ngọ": "12", "Mùi": "14", 
            "Thân": "16", "Dậu": "18", "Tuất": "20", "Hợi": "22",
            "Không rõ": "12"  # Mặc định là 12 giờ trưa nếu không rõ
        }
        
        hour = hour_mapping.get(birth_time, "12")
        
        # Thiết lập Chrome options
        chrome_options = Options()
        chrome_options.add_argument("--headless")  # Chạy ẩn
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        
        # Gửi thông báo tiến trình
        processing_msg = bot.send_message(
            user_id, 
            "⏳ *Đang lập lá số tử vi...*\n\nĐang khởi tạo trình duyệt...",
            parse_mode='Markdown'
        )
        
        # Khởi tạo trình duyệt
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Cập nhật tiến trình
        send_progress_update(user_id, processing_msg.message_id, "Đang truy cập trang web lập lá số...", 10)
        
        # Truy cập trang web
        driver.get("https://tuvivietnam.vn/lasotuvi/")
        
        # Đợi trang web tải xong
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "txtHoTen"))
        )
        
        # Cập nhật tiến trình
        send_progress_update(user_id, processing_msg.message_id, "Đang điền thông tin vào form...", 30)
        
        # Điền thông tin vào form
        # Họ tên
        name_input = driver.find_element(By.ID, "txtHoTen")
        name_input.send_keys("Học Tử Vi Bot")
        
        # Chọn giới tính
        if gender == "Nam":
            driver.find_element(By.ID, "radNam").click()
        else:
            driver.find_element(By.ID, "radNu").click()
        
        # Chọn loại lịch (mặc định là dương lịch)
        driver.find_element(By.ID, "duong_lich").click()
        
        # Chọn năm sinh
        year_select = Select(driver.find_element(By.ID, "inam_duong"))
        year_select.select_by_value(str(year))
        
        # Chọn tháng sinh
        month_select = Select(driver.find_element(By.ID, "ithang_duong"))
        month_select.select_by_value(f"{month:02d}")
        
        # Chọn ngày sinh
        day_select = Select(driver.find_element(By.ID, "ingay_duong"))
        day_select.select_by_value(f"{day:02d}")
        
        # Chọn giờ sinh
        hour_select = Select(driver.find_element(By.ID, "gio_duong"))
        hour_select.select_by_value(hour)
        
        # Chọn phút sinh (mặc định 0)
        minute_select = Select(driver.find_element(By.ID, "phut_duong"))
        minute_select.select_by_value("00")
        
        # Chọn năm xem hạn (mặc định năm hiện tại)
        current_year = datetime.now().year
        year_xem_select = Select(driver.find_element(By.ID, "selNamXemD"))
        year_xem_select.select_by_value(str(current_year))
        
        # Chọn kiểu ảnh màu
        driver.find_element(By.ID, "radMau").click()
        
        # Chọn thời gian lưu ảnh (30 ngày)
        driver.find_element(By.ID, "radluu").click()
        
        # Không cảnh báo múi giờ
        driver.find_element(By.ID, "canhbao_no").click()
        
        # Đảm bảo đánh dấu vào ô đồng ý
        confirm_checkbox = driver.find_element(By.ID, "iconfirm1")
        if not confirm_checkbox.is_selected():
            confirm_checkbox.click()
        
        # Cập nhật tiến trình
        send_progress_update(user_id, processing_msg.message_id, "Đang gửi thông tin và chờ kết quả...", 50)
        
        # Lưu số cửa sổ/tab hiện tại
        current_window_count = len(driver.window_handles)
        
        # Submit form
        submit_button = driver.find_element(By.XPATH, "//input[@value='An sao Tử Vi']")
        submit_button.click()
        
        # Đợi tab mới mở ra
        WebDriverWait(driver, 20).until(
            lambda d: len(d.window_handles) > current_window_count
        )
        
        # Chuyển sang tab mới
        driver.switch_to.window(driver.window_handles[-1])
        
        # Cập nhật tiến trình
        send_progress_update(user_id, processing_msg.message_id, "Đang tải trang kết quả...", 70)
        
        # Đợi trang tải xong
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        # Cập nhật tiến trình
        send_progress_update(user_id, processing_msg.message_id, "Đang lưu kết quả...", 80)
        
        # Lưu HTML của trang kết quả
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        html_path = f"assets/{user_id}_chart_{timestamp}.html"
        
        # Tạo thư mục assets nếu chưa tồn tại
        if not os.path.exists('assets'):
            os.makedirs('assets')
        
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        
        logger.info(f"Đã lưu HTML lá số tử vi: {html_path}")
        
        # Cập nhật tiến trình
        send_progress_update(user_id, processing_msg.message_id, "Đang trích xuất ảnh từ kết quả...", 90)
        
        # Tìm và trích xuất ảnh base64 từ HTML
        image_path = extract_base64_image_from_html(html_path, timestamp, user_id, user_data)
        if image_path:
            logger.info(f"Đã trích xuất ảnh lá số tử vi: {image_path}")
        
        # Đóng trình duyệt
        driver.quit()
        
        # Cập nhật tiến trình
        send_progress_update(user_id, processing_msg.message_id, "Hoàn tất! Đang hiển thị kết quả...", 100)
        
        # Xóa tin nhắn tiến trình
        try:
            bot.delete_message(user_id, processing_msg.message_id)
        except Exception as e:
            logger.warning(f"Không thể xóa tin nhắn tiến trình: {e}")
        
        # Cập nhật thống kê
        bot_stats['charts_created'] += 1
        
        # Trả về đường dẫn ảnh nếu đã trích xuất được, nếu không thì trả về đường dẫn HTML
        return (image_path if image_path else html_path), False  # False để đánh dấu đây là lá số mới tạo
        
    except Exception as e:
        logger.error(f"Lỗi khi lấy lá số tử vi: {e}")
        
        # Cập nhật thống kê lỗi
        bot_stats['errors'] += 1
        
        # Nếu trình duyệt đã được khởi tạo, chụp màn hình lỗi và đóng trình duyệt
        try:
            if 'driver' in locals():
                error_screenshot = f"error_{datetime.now().strftime('%Y%m%d%H%M%S')}.png"
                driver.save_screenshot(error_screenshot)
                logger.info(f"Đã chụp màn hình lỗi: {error_screenshot}")
                driver.quit()
        except:
            pass
        
        # Xóa tin nhắn tiến trình nếu có
        try:
            if 'processing_msg' in locals():
                bot.delete_message(user_id, processing_msg.message_id)
        except:
            pass
        
        # Tạo ảnh giả trong trường hợp lỗi
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        image_path = f"assets/{user_id}_chart_{timestamp}.jpg"
        
        # Tạo ảnh trống với thông tin lỗi
        img = Image.new('RGB', (800, 600), color=(255, 255, 255))
        d = ImageDraw.Draw(img)
        d.text((10, 10), f"Lá số tử vi cho người sinh ngày {day}/{month}/{year}, giờ {birth_time}, giới tính {gender}", fill=(0, 0, 0))
        d.text((10, 50), f"Có lỗi xảy ra khi lấy lá số tử vi: {str(e)}", fill=(0, 0, 0))
        d.text((10, 90), "Vui lòng thử lại sau.", fill=(0, 0, 0))
        img.save(image_path)
        
        return image_path, False

def html_to_image(html_path, user_id):
    """Chuyển đổi file HTML thành ảnh với định dạng tên file theo user_id"""
    try:
        # Thiết lập Chrome options
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        
        # Khởi tạo trình duyệt
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Mở file HTML
        driver.get(f"file://{os.path.abspath(html_path)}")
        
        # Đợi trang tải xong
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        # Tăng số lượng lá số cho user_id
        if user_id not in user_chart_counts:
            user_chart_counts[user_id] = 1
        else:
            user_chart_counts[user_id] += 1
        
        # Lưu screenshot theo cấu trúc /assets/{userid}_{số thứ tự}
        screenshot_path = f"assets/{user_id}_{user_chart_counts[user_id]}.png"
        
        driver.save_screenshot(screenshot_path)
        
        # Đóng trình duyệt
        driver.quit()
        
        return screenshot_path
    
    except Exception as e:
        logger.error(f"Lỗi khi chuyển HTML thành ảnh: {e}")
        raise

def analyze_chart_with_gpt(chart_path, user_data):
    """
    Phân tích lá số tử vi bằng AI thông qua AIRouter.
    
    Args:
        chart_path (str): Đường dẫn đến file lá số (hình ảnh)
        user_data (dict): Thông tin người dùng
        
    Returns:
        str: Kết quả phân tích
    """
    try:
        # Kiểm tra xem file có tồn tại không
        if not os.path.exists(chart_path):
            logger.error(f"File không tồn tại: {chart_path}")
            return "Không tìm thấy lá số để phân tích. Vui lòng thử lại."
        
        # Đọc file hình ảnh và chuyển sang base64
        with open(chart_path, 'rb') as img_file:
            base64_image = base64.b64encode(img_file.read()).decode('utf-8')
        
        # Lấy thông tin từ user_data
        name = user_data.get('name', 'Không xác định')
        birth_date = user_data.get('birth_date', 'Không xác định')
        birth_time = user_data.get('birth_time', 'Không xác định')
        gender = user_data.get('gender', 'Không xác định')
        
        # Chuẩn bị prompt
        system_prompt = """Bạn là một nhà chiêm tinh học chuyên nghiệp với kiến thức sâu rộng về tử vi Việt Nam. 
        Hãy phân tích lá số tử vi trong hình ảnh được cung cấp và đưa ra những nhận định chính xác, chi tiết.
        Phân tích nên bao gồm:
        1. Tổng quan về mệnh cục
        2. Phân tích các sao chính và vị trí của chúng
        3. Phân tích các cung quan trọng (Mệnh, Tài, Quan, Phu/Thê)
        4. Các góc hợp đáng chú ý và ý nghĩa của chúng
        5. Những điểm mạnh và điểm yếu trong lá số
        6. Lời khuyên dựa trên lá số
        
        Hãy viết bằng tiếng Việt, thân thiện và dễ hiểu. Đảm bảo phân tích sâu sắc nhưng không quá phức tạp để người dùng có thể hiểu được."""
        
        # Tạo nội dung user prompt
        user_prompt = f"""Thông tin cá nhân:
        - Tên: {name}
        - Ngày sinh: {birth_date}
        - Giờ sinh: {birth_time}
        - Giới tính: {gender}
        
        Hình ảnh đính kèm là lá số tử vi. Hãy phân tích lá số này một cách chi tiết."""
        
        logger.info(f"Đang phân tích lá số cho người dùng: {name}")
        
        # Gọi API để lấy phân tích
        response = openai_client.chat.completions.create(
            model="auto",  # AIRouter sẽ tự chọn mô hình phù hợp
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]}
            ],
            temperature=0.7,
            max_tokens=2000
        )
        
        # Trích xuất và trả về phân tích
        analysis = response.choices[0].message.content
        logger.info(f"AIRouter đã phân tích xong lá số, model: {response.model}")
        return analysis
        
    except Exception as e:
        logger.error(f"Lỗi khi phân tích lá số: {e}")
        return "Đã xảy ra lỗi khi phân tích lá số. Vui lòng thử lại sau."

def format_analysis(analysis, user_data):
    """
    Định dạng kết quả phân tích từ AIRouter để hiển thị đẹp hơn.
    
    Args:
        analysis (str): Phân tích từ API
        user_data (dict): Thông tin người dùng
        
    Returns:
        str: Phân tích đã được định dạng
    """
    try:
        # Thêm tiêu đề và thông tin người dùng
        name = user_data.get('name', 'Không xác định')
        birth_date = user_data.get('birth_date', 'Không xác định')
        birth_time = user_data.get('birth_time', 'Không xác định') 
        gender = user_data.get('gender', 'Không xác định')
        
        formatted_text = f"""🔮 *PHÂN TÍCH LÁ SỐ TỬ VI*
        
📋 *Thông tin cá nhân*
👤 Tên: {name}
📅 Ngày sinh: {birth_date}
🕰 Giờ sinh: {birth_time}
⚧ Giới tính: {gender}

{analysis}

✨ *Phân tích này được thực hiện bởi AIRouter, dựa trên dữ liệu lá số của bạn.*
"""
        return formatted_text
        
    except Exception as e:
        logger.error(f"Lỗi khi định dạng phân tích: {e}")
        return analysis  # Trả về phân tích gốc nếu có lỗi

@bot.message_handler(commands=['cancel'])
def cancel(message):
    """Hủy hội thoại."""
    chat_id = message.chat.id
    bot.send_message(
        chat_id,
        "❌ *Đã hủy thao tác*\n\nGõ /start để bắt đầu lại hoặc /help để xem hướng dẫn.",
        parse_mode='Markdown'
    )
    if chat_id in user_states:
        del user_states[chat_id]

@bot.message_handler(commands=['help'])
def help_command(message):
    """Hiển thị hướng dẫn sử dụng."""
    chat_id = message.chat.id
    help_text = (
        "🔮 *HƯỚNG DẪN SỬ DỤNG BOT TỬ VI* 🔮\n\n"
        "Bot này giúp bạn lập và phân tích lá số tử vi dựa trên thông tin ngày sinh. Các lệnh cơ bản:\n\n"
        "• /start - Bắt đầu lập lá số tử vi\n"
        "• /cancel - Hủy thao tác hiện tại\n"
        "• /help - Hiển thị hướng dẫn này\n\n"
        "✨ *Quy trình sử dụng*:\n"
        "1. Nhập ngày tháng năm sinh (DD/MM/YYYY)\n"
        "2. Chọn giờ sinh (theo 12 con giáp)\n"
        "3. Chọn giới tính\n"
        "4. Đợi bot lập lá số tử vi\n"
        "5. Chọn 'Phân tích lá số' để nhận được luận giải chi tiết\n\n"
        "🔍 *Lưu ý*: Để có kết quả chính xác, vui lòng cung cấp thông tin đầy đủ và chính xác."
    )
    
    bot.send_message(
        chat_id,
        help_text,
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['stats'])
def stats_command(message):
    """Hiển thị thống kê sử dụng bot (chỉ dành cho admin)."""
    chat_id = message.chat.id
    
    # Danh sách ID admin (có thể đưa vào biến môi trường)
    admin_ids = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()]
    
    # Nếu không có admin nào được cấu hình, cho phép bất kỳ ai xem thống kê
    if not admin_ids:
        send_stats_to_admin(chat_id)
        return
    
    # Kiểm tra xem người dùng có phải là admin không
    if chat_id in admin_ids:
        send_stats_to_admin(chat_id)
    else:
        bot.send_message(
            chat_id,
            "⚠️ *Bạn không có quyền xem thống kê*\n\nChỉ admin mới có thể sử dụng lệnh này.",
            parse_mode='Markdown'
        )

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    """Xử lý các tin nhắn không rõ."""
    chat_id = message.chat.id
    if chat_id not in user_states:
        bot.send_message(
            chat_id,
            "🤔 Bot không hiểu yêu cầu của bạn.\n\n"
            "• Gõ /start để bắt đầu lập lá số tử vi\n"
            "• Gõ /help để xem hướng dẫn sử dụng",
            parse_mode='Markdown'
        )
    else:
        bot.send_message(
            chat_id,
            "⚠️ Vui lòng làm theo hướng dẫn hoặc gõ /cancel để hủy thao tác hiện tại.",
            parse_mode='Markdown'
        )

def process_analysis(chat_id):
    """Xử lý phân tích lá số tử vi."""
    if chat_id not in user_states:
        bot.send_message(
            chat_id, 
            "❌ *Không tìm thấy lá số tử vi*\n\nVui lòng gõ /start để bắt đầu lại.",
            parse_mode='Markdown'
        )
        return
    
    # Kiểm tra xem có đường dẫn ảnh hoặc HTML không
    if 'chart_image_path' not in user_states[chat_id] and 'chart_html_path' not in user_states[chat_id]:
        bot.send_message(
            chat_id, 
            "❌ *Không tìm thấy lá số tử vi*\n\nVui lòng gõ /start để bắt đầu lại.",
            parse_mode='Markdown'
        )
        return
    
    # Gửi thông báo đang phân tích
    processing_msg = bot.send_message(
        chat_id, 
        "⏳ *Đang phân tích lá số tử vi...*\n\nVui lòng đợi trong giây lát, quá trình này có thể mất 30-60 giây.",
        parse_mode='Markdown'
    )
    
    try:
        # Lấy đường dẫn ảnh hoặc HTML từ trạng thái người dùng
        if 'chart_image_path' in user_states[chat_id]:
            chart_path = user_states[chat_id]['chart_image_path']
        else:
            chart_path = user_states[chat_id]['chart_html_path']
            # Nếu là HTML, chuyển đổi thành ảnh
            if chart_path.endswith('.html'):
                # Thử trích xuất ảnh base64 từ HTML
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                image_path = extract_base64_image_from_html(chart_path, chat_id, user_states[chat_id])
                if image_path:
                    chart_path = image_path
                else:
                    # Nếu không trích xuất được, chuyển HTML thành ảnh
                    chart_path = html_to_image(chart_path, chat_id)
                user_states[chat_id]['chart_image_path'] = chart_path
        
        # Phân tích lá số tử vi
        analysis = analyze_chart_with_gpt(chart_path, user_states[chat_id])
        
        # Xóa thông báo đang xử lý
        try:
            bot.delete_message(chat_id, processing_msg.message_id)
        except Exception as e:
            logger.warning(f"Không thể xóa tin nhắn 'đang xử lý': {e}")
        
        # Gửi phân tích cho người dùng
        # Phân tích có thể dài, cần chia thành nhiều phần nếu vượt quá giới hạn
        max_length = 4000  # Giới hạn tin nhắn Telegram
        
        if len(analysis) <= max_length:
            try:
                bot.send_message(chat_id, analysis, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Lỗi khi gửi phân tích: {e}")
                # Gửi không có định dạng Markdown nếu có lỗi
                try:
                    bot.send_message(chat_id, "❌ Không thể hiển thị định dạng đẹp, gửi dạng văn bản thường.")
                    bot.send_message(chat_id, analysis)
                except:
                    bot.send_message(chat_id, "❌ Phân tích quá dài. Vui lòng xem từng phần chi tiết bên dưới.")
        else:
            # Chia phân tích thành nhiều phần
            parts = [analysis[i:i+max_length] for i in range(0, len(analysis), max_length)]
            for i, part in enumerate(parts):
                if i == 0:  # Phần đầu tiên
                    bot.send_message(chat_id, part, parse_mode='Markdown')
                else:  # Các phần tiếp theo
                    bot.send_message(chat_id, f"(tiếp theo)...\n\n{part}", parse_mode='Markdown')
        
        # Thêm lựa chọn xem chi tiết từng khía cạnh
        chart_id = save_chart_for_details(chat_id, user_states[chat_id], chart_path)
        
        detail_markup = types.InlineKeyboardMarkup(row_width=2)
        detail_markup.add(
            types.InlineKeyboardButton("💰 Sự nghiệp & Tài lộc", callback_data=f"detail_career_{chart_id}"),
            types.InlineKeyboardButton("❤️ Tình duyên & Hôn nhân", callback_data=f"detail_love_{chart_id}"),
            types.InlineKeyboardButton("🏥 Sức khỏe & Tâm lý", callback_data=f"detail_health_{chart_id}"),
            types.InlineKeyboardButton("🔑 Phương pháp khai vận", callback_data=f"detail_remedies_{chart_id}")
        )
        
        bot.send_message(
            chat_id,
            "👇 *Bạn muốn xem chi tiết về khía cạnh nào?*",
            reply_markup=detail_markup,
            parse_mode='Markdown'
        )
        
        # Cập nhật thống kê
        bot_stats['analyses_performed'] += 1
        
    except Exception as e:
        logger.error(f"Lỗi khi phân tích lá số tử vi: {e}")
        try:
            bot.send_message(
                chat_id,
                "❌ *Đã xảy ra lỗi khi phân tích lá số tử vi*\n\nVui lòng thử lại sau.",
                parse_mode='Markdown'
            )
            # Xóa thông báo đang xử lý
            bot.delete_message(chat_id, processing_msg.message_id)
        except Exception as delete_error:
            logger.warning(f"Không thể xóa tin nhắn hoặc gửi thông báo lỗi: {delete_error}")
        # Cập nhật thống kê lỗi
        bot_stats['errors'] += 1

def save_chart_for_details(chat_id, user_data, chart_path):
    """Lưu thông tin lá số để sử dụng cho chi tiết các khía cạnh"""
    conn = get_db_connection()
    if not conn:
        # Trả về một ID ngẫu nhiên nếu không kết nối được CSDL
        return str(uuid.uuid4())
    
    try:
        # Lấy base64 image từ file
        with open(chart_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        
        cursor = conn.cursor()
        
        # Lưu vào database nếu chưa có
        cursor.execute("""
            INSERT INTO charts (user_id, day, month, year, birth_time, gender, chart_image)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            chat_id, 
            user_data['day'], 
            user_data['month'], 
            user_data['year'], 
            user_data['birth_time'], 
            user_data['gender'], 
            base64_image
        ))
        
        result = cursor.fetchone()
        chart_id = result[0] if result else str(uuid.uuid4())
        
        return chart_id
    except Exception as e:
        logger.error(f"Lỗi khi lưu lá số: {e}")
        return str(uuid.uuid4())  # Trả về ID ngẫu nhiên trong trường hợp lỗi
    finally:
        if conn and hasattr(conn, 'close'):
            try:
                conn.close()
            except:
                pass

def extract_base64_image_from_html(html_path, timestamp, user_id, user_data):
    """
    Trích xuất ảnh base64 từ file HTML, lưu file và lưu vào cơ sở dữ liệu
    
    Args:
        html_path (str): Đường dẫn đến file HTML
        timestamp (str): Timestamp để đặt tên file
        user_id (int): ID của người dùng
        user_data (dict): Thông tin người dùng
        
    Returns:
        str: Đường dẫn đến file ảnh đã lưu, hoặc None nếu không thành công
    """
    try:
        # Đọc nội dung file HTML
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # Tìm tất cả các chuỗi data:image/jpeg;base64 hoặc data:image/png;base64
        pattern = r'data:image/[^;]+;base64,([^"\']+)'
        matches = re.findall(pattern, html_content)
        
        if not matches:
            logger.warning(f"Không tìm thấy ảnh base64 trong HTML: {html_path}")
            
            # Thử tìm với các pattern khác
            soup = BeautifulSoup(html_content, 'html.parser')
            img_tags = soup.find_all('img')
            
            if img_tags:
                for img in img_tags:
                    src = img.get('src', '')
                    if src.startswith('data:image'):
                        # Trích xuất phần base64
                        base64_data = src.split(',')[1] if ',' in src else ''
                        if base64_data:
                            matches = [base64_data]
                            logger.info("Đã tìm thấy ảnh base64 từ thẻ img")
                            break
            
            if not matches:
                logger.error("Không thể tìm thấy ảnh base64 trong HTML sau khi thử nhiều cách")
                return None
        
        # Tăng số lượng lá số cho user_id
        if user_id not in user_chart_counts:
            user_chart_counts[user_id] = 1
        else:
            user_chart_counts[user_id] += 1
        
        # Lưu ảnh vào file
        image_path = f"assets/{user_id}_{user_chart_counts[user_id]}.jpg"
        
        # Đảm bảo thư mục assets tồn tại
        if not os.path.exists('assets'):
            os.makedirs('assets')
        
        # Xử lý trường hợp base64 có thể bị hỏng
        try:
            image_data = base64.b64decode(matches[0])
            with open(image_path, 'wb') as f:
                f.write(image_data)
            
            # Kiểm tra xem file ảnh có hợp lệ không
            try:
                with Image.open(image_path) as img:
                    # Nếu mở được ảnh, kiểm tra kích thước
                    width, height = img.size
                    if width < 10 or height < 10:
                        logger.warning(f"Ảnh quá nhỏ: {width}x{height}, có thể không hợp lệ")
                        # Vẫn giữ lại ảnh để kiểm tra
            except Exception as img_error:
                logger.error(f"Ảnh không hợp lệ: {img_error}")
                # Xóa file ảnh không hợp lệ
                os.remove(image_path)
                return None
        except Exception as decode_error:
            logger.error(f"Lỗi khi giải mã base64: {decode_error}")
            return None
        
        logger.info(f"Đã lưu ảnh từ base64 cho user {user_id}: {image_path}")
        
        # Lưu thông tin và base64 vào cơ sở dữ liệu
        try:
            save_chart(user_id, user_data, matches[0])
        except Exception as db_error:
            logger.warning(f"Không thể lưu chart vào database: {db_error}")
            # Vẫn tiếp tục vì đã lưu được ảnh
        
        return image_path
    
    except Exception as e:
        logger.error(f"Lỗi khi trích xuất ảnh base64: {e}")
        return None

def test_airouter():
    """
    Kiểm tra kết nối với AIRouter.
    """
    try:
        logger.info("Kiểm tra kết nối AIRouter...")
        response = openai_client.chat.completions.create(
            model="auto",
            messages=[
                {"role": "system", "content": "Bạn là một trợ lý AI hữu ích."},
                {"role": "user", "content": "Chào bạn, đây là tin nhắn kiểm tra kết nối. Trả lời ngắn gọn."}
            ],
            max_tokens=50
        )
        logger.info(f"Kết nối AIRouter thành công! Model được sử dụng: {response.model}")
        return True
    except Exception as e:
        logger.error(f"Lỗi kết nối AIRouter: {e}")
        return False

# Thêm hàm dọn dẹp file tạm định kỳ
def cleanup_temp_files(max_age_days=7):
    """
    Dọn dẹp các file tạm thời đã cũ trong thư mục assets
    
    Args:
        max_age_days (int): Số ngày tối đa để giữ file, mặc định là 7 ngày
    """
    try:
        logger.info(f"Bắt đầu dọn dẹp file tạm cũ hơn {max_age_days} ngày")
        
        # Kiểm tra thư mục assets
        if not os.path.exists('assets'):
            logger.info("Thư mục assets không tồn tại, không cần dọn dẹp")
            return
        
        # Lấy thời gian hiện tại
        current_time = time.time()
        max_age_seconds = max_age_days * 24 * 60 * 60
        
        # Đếm số file đã xóa
        deleted_count = 0
        
        # Duyệt qua tất cả file trong thư mục assets
        for filename in os.listdir('assets'):
            file_path = os.path.join('assets', filename)
            
            # Bỏ qua nếu là thư mục
            if os.path.isdir(file_path):
                continue
            
            # Kiểm tra tuổi của file
            file_age = current_time - os.path.getmtime(file_path)
            
            # Xóa file nếu quá cũ
            if file_age > max_age_seconds:
                try:
                    os.remove(file_path)
                    deleted_count += 1
                    logger.debug(f"Đã xóa file cũ: {file_path}")
                except Exception as e:
                    logger.warning(f"Không thể xóa file {file_path}: {e}")
        
        logger.info(f"Đã dọn dẹp {deleted_count} file tạm cũ")
    
    except Exception as e:
        logger.error(f"Lỗi khi dọn dẹp file tạm: {e}")

def main():
    """
    Hàm chính để chạy bot.
    """
    try:
        # Đặt lại thống kê khi khởi động
        global bot_stats
        bot_stats = {
            'start_time': datetime.now(),
            'charts_created': 0,
            'charts_reused': 0,
            'analyses_performed': 0,
            'errors': 0
        }
        
        # Kiểm tra thư mục
        if not os.path.exists('assets'):
            os.makedirs('assets')
        if not os.path.exists('assets/charts'):
            os.makedirs('assets/charts')
        if not os.path.exists('assets/temp'):
            os.makedirs('assets/temp')
            
        # Dọn dẹp file tạm cũ khi khởi động
        cleanup_temp_files()
            
        # Tạo cơ sở dữ liệu tạm thời nếu cần
        create_temporary_database()
        
        # Kiểm tra kết nối cơ sở dữ liệu
        db_conn = get_db_connection()
        if db_conn:
            logger.info("Kết nối cơ sở dữ liệu thành công")
            db_conn.close()
        else:
            logger.error("Không thể kết nối đến cơ sở dữ liệu")
        
        # Khởi tạo cơ sở dữ liệu
        init_database()
        
        # Kiểm tra kết nối AIRouter
        if test_airouter():
            logger.info("Kết nối AIRouter thành công, bot sẵn sàng sử dụng AI phân tích")
        else:
            logger.warning("Không thể kết nối đến AIRouter, một số chức năng phân tích có thể không hoạt động")
        
        # Gửi thông báo khởi động cho admin
        admin_ids = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()]
        for admin_id in admin_ids:
            try:
                bot.send_message(
                    admin_id,
                    f"🚀 *Bot Tử Vi đã khởi động*\n\n⏱ Thời gian: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.warning(f"Không thể gửi thông báo khởi động cho admin {admin_id}: {e}")
        
        # Khởi động bot
        logger.info("Bot đang khởi động...")
        bot.polling(none_stop=True)
        
    except Exception as e:
        logger.error(f"Lỗi khi khởi động bot: {e}")
        # Thử khởi động lại sau 5 giây
        time.sleep(5)
        main()

@bot.callback_query_handler(func=lambda call: call.data.startswith("detail_"))
def handle_detail_request(call):
    """Xử lý yêu cầu xem chi tiết về một khía cạnh cụ thể."""
    chat_id = call.message.chat.id
    aspect = call.data.split("_")[1]
    
    # Gửi thông báo đang phân tích
    processing_msg = bot.send_message(
        chat_id, 
        "⏳ *Đang phân tích chi tiết về {get_aspect_name(aspect)}...*\n\nVui lòng đợi trong giây lát.",
        parse_mode='Markdown'
    )
    
    try:
        # Lấy thông tin từ user_data (có thể lưu tạm vào session để tái sử dụng)
        day = call.message.text.split("Ngày sinh:")[1].split("\n")[0].strip() if "Ngày sinh:" in call.message.text else "không rõ"
        # Tương tự cho tháng, năm, giờ sinh, giới tính
        
        # Phân tích chi tiết bằng AI
        detailed_analysis = get_detailed_analysis(aspect, day)
        
        # Xóa thông báo đang xử lý
        bot.delete_message(chat_id, processing_msg.message_id)
        
        # Gửi phân tích chi tiết
        bot.send_message(
            chat_id,
            detailed_analysis,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Lỗi khi phân tích chi tiết: {e}")
        bot.send_message(
            chat_id,
            "❌ *Đã xảy ra lỗi khi phân tích chi tiết*\n\nVui lòng thử lại sau.",
            parse_mode='Markdown'
        )
        # Xóa thông báo đang xử lý
        bot.delete_message(chat_id, processing_msg.message_id)

def get_aspect_name(aspect):
    """Lấy tên đầy đủ của khía cạnh."""
    mapping = {
        "career": "Sự nghiệp",
        "love": "Tình duyên",
        "wealth": "Tài lộc",
        "health": "Sức khỏe"
    }
    return mapping.get(aspect, aspect)

def get_detailed_analysis(aspect, day):
    """Lấy phân tích chi tiết về một khía cạnh cụ thể."""
    # Phân tích chi tiết bằng AI - tương tự như analyze_chart_with_gpt
    # ...
    
    # Demo return
    return f"✨ *CHI TIẾT VỀ {get_aspect_name(aspect).upper()}* ✨\n\n" + \
           f"Đây là phân tích chi tiết về {get_aspect_name(aspect).lower()} cho người sinh ngày {day}.\n\n" + \
           "• Phân tích chi tiết sẽ được thực hiện dựa trên các sao trong lá số.\n" + \
           "• Bot đang trong quá trình hoàn thiện tính năng này."

def save_user(user):
    """Lưu thông tin người dùng vào cơ sở dữ liệu"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (telegram_id, first_name, last_name, username)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (telegram_id) 
            DO UPDATE SET 
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                username = EXCLUDED.username
            RETURNING id
        """, (user.id, user.first_name, user.last_name, user.username))
        
        result = cursor.fetchone()
        logger.info(f"Đã lưu thông tin người dùng {user.id}")
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Lỗi khi lưu thông tin người dùng: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

def save_chart(user_id, chart_data, base64_image):
    """Lưu lá số tử vi và hình ảnh base64 vào cơ sở dữ liệu"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO charts (user_id, day, month, year, birth_time, gender, chart_image)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            user_id, 
            chart_data['day'], 
            chart_data['month'], 
            chart_data['year'], 
            chart_data['birth_time'], 
            chart_data['gender'], 
            base64_image
        ))
        
        result = cursor.fetchone()
        logger.info(f"Đã lưu lá số tử vi cho user {user_id}")
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Lỗi khi lưu lá số tử vi: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

def get_user_charts(user_id, limit=5):
    """Lấy lịch sử lá số tử vi của người dùng"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT id, day, month, year, birth_time, gender, created_at
            FROM charts
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (user_id, limit))
        
        return cursor.fetchall()
    except Exception as e:
        logger.error(f"Lỗi khi lấy lịch sử lá số tử vi: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

def get_chart_image(chart_id):
    """Lấy hình ảnh lá số tử vi từ ID"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chart_image
            FROM charts
            WHERE id = %s
        """, (chart_id,))
        
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Lỗi khi lấy hình ảnh lá số tử vi: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

@bot.message_handler(commands=['history'])
def history_command(message):
    """Hiển thị lịch sử lá số tử vi của người dùng."""
    chat_id = message.chat.id
    
    # Lấy lịch sử lá số tử vi
    charts = get_user_charts(chat_id)
    
    if not charts:
        bot.send_message(
            chat_id,
            "🔍 *Bạn chưa có lá số tử vi nào*\n\n"
            "Gõ /start để bắt đầu lập lá số mới.",
            parse_mode='Markdown'
        )
        return
    
    # Tạo thông báo lịch sử
    history_message = "📜 *LỊCH SỬ LÁ SỐ TỬ VI CỦA BẠN*\n\n"
    
    for i, chart in enumerate(charts, 1):
        date_created = chart['created_at'].strftime("%d/%m/%Y %H:%M")
        history_message += f"{i}. Ngày sinh: {chart['day']}/{chart['month']}/{chart['year']}, "\
                          f"Giờ sinh: {chart['birth_time']}, "\
                          f"Giới tính: {chart['gender']}\n"\
                          f"   Ngày lập: {date_created}\n\n"
    
    # Tạo inline keyboard để xem lại các lá số
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    for i, chart in enumerate(charts, 1):
        markup.add(types.InlineKeyboardButton(
            f"Xem lại lá số {i}", 
            callback_data=f"view_chart_{chart['id']}"
        ))
    
    bot.send_message(
        chat_id,
        history_message,
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("view_chart_"))
def handle_view_chart(call):
    """Xử lý yêu cầu xem lại lá số tử vi."""
    chat_id = call.message.chat.id
    chart_id = int(call.data.split("_")[2])
    
    # Lấy hình ảnh lá số
    base64_image = get_chart_image(chart_id)
    
    if not base64_image:
        bot.send_message(
            chat_id,
            "❌ *Không tìm thấy lá số tử vi*",
            parse_mode='Markdown'
        )
        return
    
    # Lưu ảnh tạm để gửi
    temp_image_path = f"temp_analysis_{chart_id}.jpg"
    with open(temp_image_path, 'wb') as f:
        f.write(base64.b64decode(base64_image))
    
    # Gửi ảnh cho người dùng
    with open(temp_image_path, 'rb') as photo:
        bot.send_photo(
            chat_id,
            photo,
            caption="✨ *Lá số tử vi của bạn*",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("🔮 Phân tích lá số", callback_data=f"analyze_chart_{chart_id}")
            ),
            parse_mode='Markdown'
        )
    
    # Xóa file tạm
    os.remove(temp_image_path)

@bot.callback_query_handler(func=lambda call: call.data.startswith("analyze_chart_"))
def handle_analyze_chart(call):
    """Xử lý yêu cầu phân tích lá số tử vi từ lịch sử."""
    chat_id = call.message.chat.id
    chart_id = int(call.data.split("_")[2])
    
    # Gửi thông báo đang phân tích
    processing_msg = bot.send_message(
        chat_id, 
        "⏳ *Đang phân tích lá số tử vi...*\n\nVui lòng đợi trong giây lát, quá trình này có thể mất 30-60 giây.",
        parse_mode='Markdown'
    )
    
    try:
        # Lấy thông tin lá số từ cơ sở dữ liệu
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT day, month, year, birth_time, gender, chart_image
            FROM charts
            WHERE id = %s
        """, (chart_id,))
        
        chart_data = cursor.fetchone()
        
        if not chart_data:
            bot.send_message(
                chat_id,
                "❌ *Không tìm thấy lá số tử vi*",
                parse_mode='Markdown'
            )
            bot.delete_message(chat_id, processing_msg.message_id)
            return
        
        # Lưu ảnh tạm để phân tích
        temp_image_path = f"temp_analysis_{chart_id}.jpg"
        with open(temp_image_path, 'wb') as f:
            f.write(base64.b64decode(chart_data['chart_image']))
        
        # Phân tích lá số
        analysis = analyze_chart_with_gpt(temp_image_path, chart_data)
        
        # Xóa thông báo đang xử lý
        bot.delete_message(chat_id, processing_msg.message_id)
        
        # Gửi phân tích cho người dùng
        # Phân tích có thể dài, cần chia thành nhiều phần nếu vượt quá giới hạn
        max_length = 4000  # Giới hạn tin nhắn Telegram
        
        if len(analysis) <= max_length:
            bot.send_message(chat_id, analysis, parse_mode='Markdown')
        else:
            # Chia phân tích thành nhiều phần
            parts = [analysis[i:i+max_length] for i in range(0, len(analysis), max_length)]
            for i, part in enumerate(parts):
                if i == 0:  # Phần đầu tiên
                    bot.send_message(chat_id, part, parse_mode='Markdown')
                else:  # Các phần tiếp theo
                    bot.send_message(chat_id, f"(tiếp theo)...\n\n{part}", parse_mode='Markdown')
        
        # Thêm lựa chọn xem chi tiết từng khía cạnh
        detail_markup = types.InlineKeyboardMarkup(row_width=2)
        detail_markup.add(
            types.InlineKeyboardButton("💰 Sự nghiệp & Tài lộc", callback_data=f"detail_career_{chart_id}"),
            types.InlineKeyboardButton("❤️ Tình duyên & Hôn nhân", callback_data=f"detail_love_{chart_id}"),
            types.InlineKeyboardButton("🏥 Sức khỏe & Tâm lý", callback_data=f"detail_health_{chart_id}"),
            types.InlineKeyboardButton("🔑 Phương pháp khai vận", callback_data=f"detail_remedies_{chart_id}")
        )
        
        bot.send_message(
            chat_id,
            "👇 *Bạn muốn xem chi tiết về khía cạnh nào?*",
            reply_markup=detail_markup,
            parse_mode='Markdown'
        )
        
        # Xóa file tạm
        os.remove(temp_image_path)
        
    except Exception as e:
        logger.error(f"Lỗi khi phân tích lá số tử vi: {e}")
        bot.send_message(
            chat_id,
            "❌ *Đã xảy ra lỗi khi phân tích lá số tử vi*\n\nVui lòng thử lại sau.",
            parse_mode='Markdown'
        )
        # Xóa thông báo đang xử lý
        bot.delete_message(chat_id, processing_msg.message_id)

def check_existing_chart(user_id, day, month, year, birth_time, gender):
    """
    Kiểm tra xem lá số với thông tin tương tự đã tồn tại trong cơ sở dữ liệu chưa.
    
    Args:
        user_id (int): ID của người dùng
        day (int): Ngày sinh
        month (int): Tháng sinh
        year (int): Năm sinh
        birth_time (str): Giờ sinh
        gender (str): Giới tính
        
    Returns:
        tuple: (chart_exists, chart_path, chart_id) - Trạng thái tồn tại, đường dẫn và ID của lá số
    """
    try:
        # Kết nối đến cơ sở dữ liệu
        conn = get_db_connection()
        if not conn:
            logger.warning("Không thể kết nối đến cơ sở dữ liệu để kiểm tra lá số tồn tại")
            return False, None, None
        
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Tìm kiếm lá số với thông tin tương tự
        cursor.execute("""
            SELECT id, chart_image FROM charts 
            WHERE user_id = %s AND day = %s AND month = %s AND year = %s 
            AND birth_time = %s AND gender = %s
            ORDER BY created_at DESC LIMIT 1
        """, (user_id, day, month, year, birth_time, gender))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            logger.info(f"Đã tìm thấy lá số tồn tại cho user {user_id} với thông tin: {day}/{month}/{year}, {birth_time}, {gender}")
            
            # Kiểm tra xem có đường dẫn hình ảnh không
            chart_id = result['id']
            chart_image = result['chart_image']
            
            # Nếu chart_image là base64, cần lưu lại thành file
            if chart_image and chart_image.startswith('data:image') or len(chart_image) > 200:
                # Đây là base64, cần lưu thành file
                image_path = f"assets/{user_id}_{chart_id}.jpg"
                
                # Kiểm tra xem file đã tồn tại chưa
                if not os.path.exists(image_path):
                    # Trích xuất phần base64 thực sự
                    if ',' in chart_image:
                        base64_data = chart_image.split(',')[1]
                    else:
                        base64_data = chart_image
                    
                    # Lưu thành file
                    with open(image_path, 'wb') as f:
                        f.write(base64.b64decode(base64_data))
                    
                    logger.info(f"Đã lưu lại hình ảnh lá số từ base64 cho user {user_id}: {image_path}")
                
                return True, image_path, chart_id
            
            # Nếu chart_image là đường dẫn file
            elif chart_image and os.path.exists(chart_image):
                return True, chart_image, chart_id
            
            # Nếu không có hình ảnh hoặc không tìm thấy file
            else:
                logger.warning(f"Không tìm thấy hình ảnh lá số cho chart_id {chart_id}")
                return False, None, None
        
        return False, None, None
        
    except Exception as e:
        logger.error(f"Lỗi khi kiểm tra lá số tồn tại: {e}")
        return False, None, None

if __name__ == "__main__":
    main() 