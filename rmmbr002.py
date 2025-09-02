import tkinter as tk
from tkinter import filedialog, messagebox
import os
import json
import random
import threading
import shutil
import tempfile
import re
import sys
import ctypes  # Для тёмного заголовка на Windows
from docx import Document  # Requires: pip install python-docx
import pdfplumber  # Requires: pip install pdfplumber
import openpyxl  # Requires: pip install openpyxl
import pystray  # Requires: pip install pystray
from pystray import MenuItem as item
from PIL import Image  # Requires: pip install pillow

# Константы
DATA_FILE = 'definitions.json'
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 150
DEFAULT_INTERVAL = 5  # По умолчанию 5 минут
TEMP_DIR = tempfile.mkdtemp()  # Временная папка для локальных копий
DARK_BG = '#1e1e1e'
LIGHT_TEXT = '#ffffff'
ACTIVE_BG = '#555555'  # Цвет для активной кнопки интервала
FONT_SIZE = 14  # Шрифт для текста определений

# Глобальные переменные
definitions = []
current_index = 0
interval_minutes = DEFAULT_INTERVAL  # Храним интервал в минутах
interval_ms = interval_minutes * 60 * 1000  # мс для tkinter after
root = None
label = None
tray_icon = None
control_frame = None
auto_update = True  # Флаг для плей/паузы
update_id = None  # Для хранения ID after
timer_id = None  # Для хранения ID after для таймера
interval_buttons = {}  # Для хранения кнопок интервалов
topmost_enabled = True  # Флаг для поверх всех окон
timer_label = None  # Новый лейбл для таймера
time_left = interval_minutes * 60  # Оставшееся время в секундах

def resource_path(relative_path):
    """ Получить путь к ресурсу (для PyInstaller) """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def set_dark_title_bar(window):
    """Установка тёмного заголовка на Windows 10/11"""
    if sys.platform != "win32":
        return
    try:
        # Получаем HWND окна
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())

        # Включаем Dark Mode
        dark_attr = 20  # DWMWA_USE_IMMERSIVE_DARK_MODE
        value = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            dark_attr,
            ctypes.byref(value),
            ctypes.sizeof(value)
        )

        # Альтернатива для старых версий Windows 10
        # ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int(1)))  # DWMWA_CAPTION_COLOR

    except Exception as e:
        print(f"Тёмный заголовок не установлен: {e}")

def extract_definitions_from_text(text):
    """Извлечение определений из текста с учетом различных языковых структур"""
    lines = text.split('\n')
    extracted = []

    patterns = [
        r'^([^—–-]+)\s*[-—–]\s*(.+)$',
        r'^([^:]+)\s*:\s*(.+)$',
        r'^([^—–:]+)\s*(?:—|–|-)?\s*это\s*(.+)$',
        r'^([^,]+),\s*то есть\s*(.+)$',
        r'^([^\(]+)\s*\(([^)]+)\)$',
        r'^([^—–:]+)\s*(?:—|–|-)\s*(.+)$',
    ]

    for line in lines:
        line = line.strip()
        if not line:
            continue

        matched = False
        for pattern in patterns:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                term, definition = match.groups()
                term = term.strip()
                definition = definition.strip()
                if len(term) > 1 and len(definition) > 3:
                    extracted.append(f"{term} - {definition}")
                    matched = True
                    break

        if not matched:
            for marker in ['является', 'означает', 'представляет собой']:
                if marker in line.lower():
                    parts = line.split(marker, 1)
                    if len(parts) == 2:
                        term, definition = parts
                        term = term.strip()
                        definition = definition.strip()
                        if len(term) > 1 and len(definition) > 3:
                            extracted.append(f"{term} - {definition}")
                            matched = True
                            break

    return list(set(extracted))


def copy_file_locally(file_path):
    """Копирование файла во временную папку для избежания проблем с OneDrive"""
    try:
        local_path = os.path.join(TEMP_DIR, os.path.basename(file_path))
        shutil.copy2(file_path, local_path)
        return local_path
    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось скопировать файл {file_path}: {e}")
        return None


def parse_text(file_path):
    local_path = copy_file_locally(file_path)
    if not local_path:
        return []
    try:
        with open(local_path, 'r', encoding='utf-8') as f:
            text = f.read()
        return extract_definitions_from_text(text)
    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось обработать .txt файл: {e}")
        return []


def parse_word(file_path):
    local_path = copy_file_locally(file_path)
    if not local_path:
        return []
    try:
        doc = Document(local_path)
        text = '\n'.join([para.text for para in doc.paragraphs if para.text.strip()])
        return extract_definitions_from_text(text)
    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось обработать .docx файл: {e}")
        return []


def parse_pdf(file_path):
    local_path = copy_file_locally(file_path)
    if not local_path:
        return []
    try:
        with pdfplumber.open(local_path) as pdf:
            text = '\n'.join([
                page.extract_text() or '' for page in pdf.pages
            ])
        return extract_definitions_from_text(text)
    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось обработать .pdf файл: {e}")
        return []


def parse_excel(file_path):
    local_path = copy_file_locally(file_path)
    if not local_path:
        return []
    try:
        wb = openpyxl.load_workbook(local_path, read_only=True)
        text = ''
        for sheet in wb:
            for row in sheet.iter_rows(values_only=True):
                text += ' '.join([str(cell) for cell in row if cell is not None]) + '\n'
        wb.close()
        return extract_definitions_from_text(text)
    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось обработать .xlsx файл: {e}")
        return []


def load_files():
    """Загрузка определений из выбранных файлов."""
    global definitions
    file_paths = filedialog.askopenfilenames(
        title="Выберите файлы",
        filetypes=[("Документы", "*.docx *.pdf *.xlsx *.txt")]
    )
    if not file_paths:
        return False

    extracted = []
    for path in file_paths:
        ext = os.path.splitext(path)[1].lower()
        if ext == '.txt':
            extracted.extend(parse_text(path))
        elif ext == '.docx':
            extracted.extend(parse_word(path))
        elif ext == '.pdf':
            extracted.extend(parse_pdf(path))
        elif ext == '.xlsx':
            extracted.extend(parse_excel(path))

    definitions = list(set(extracted))
    if definitions:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(definitions, f, ensure_ascii=False, indent=2)
        update_gui_after_load()
        return True
    else:
        messagebox.showinfo("Информация", "Определения не найдены в выбранных файлах.")
        return False


def load_folder():
    """Загрузка определений из всех файлов в папке."""
    global definitions
    folder_path = filedialog.askdirectory(title="Выберите папку")
    if not folder_path:
        return False

    extracted = []
    for root_dir, _, files in os.walk(folder_path):
        for file in files:
            path = os.path.join(root_dir, file)
            ext = os.path.splitext(file)[1].lower()
            if ext == '.txt':
                extracted.extend(parse_text(path))
            elif ext == '.docx':
                extracted.extend(parse_word(path))
            elif ext == '.pdf':
                extracted.extend(parse_pdf(path))
            elif ext == '.xlsx':
                extracted.extend(parse_excel(path))

    definitions = list(set(extracted))
    if definitions:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(definitions, f, ensure_ascii=False, indent=2)
        update_gui_after_load()
        return True
    else:
        messagebox.showinfo("Информация", "Определения не найдены в папке.")
        return False


def load_saved_data():
    """Загрузка сохранённых определений из JSON."""
    global definitions
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                definitions = json.load(f)
            return True
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить сохраненные данные: {e}")
            return False
    return False


def update_gui_after_load():
    """Обновление интерфейса после загрузки данных."""
    global auto_update, time_left
    if definitions:
        label.config(text=definitions[0])
        if auto_update:
            stop_timers()
            time_left = interval_minutes * 60
            start_timers()
        control_frame.pack(expand=False, fill='x')
        timer_label.pack(side='right', padx=5, pady=5)
    else:
        label.config(text="Определения не найдены. Загрузите файлы.")
        timer_label.pack_forget()
    update_tray_menu()
    update_interval_buttons()


def start_timers():
    """Запуск таймеров обновления и отсчёта."""
    global update_id, timer_id
    update_id = root.after(interval_ms, update_definition)
    timer_id = root.after(1000, update_timer)


def stop_timers():
    """Остановка всех активных таймеров."""
    global update_id, timer_id
    if update_id:
        root.after_cancel(update_id)
        update_id = None
    if timer_id:
        root.after_cancel(timer_id)
        timer_id = None


def update_timer():
    """Обновление таймера каждую секунду."""
    global time_left, timer_id
    if auto_update and definitions:
        if time_left > 0:
            time_left -= 1
            mins, secs = divmod(time_left, 60)
            timer_label.config(text=f"{mins:02d}:{secs:02d}")
            timer_id = root.after(1000, update_timer)
        else:
            # Время вышло — меняем определение
            update_definition()


def update_definition():
    """Смена определения на случайное по таймеру."""
    global current_index, time_left
    if definitions and auto_update:
        current_index = get_random_index()
        label.config(text=definitions[current_index])
        # Сброс таймера
        stop_timers()
        time_left = interval_minutes * 60  # Обязательно обновляем
        start_timers()


def next_definition():
    """Показать следующее (случайное) определение."""
    global current_index, time_left
    if definitions:
        current_index = get_random_index()
        label.config(text=definitions[current_index])
        if auto_update:
            stop_timers()
            time_left = interval_minutes * 60
            start_timers()


def prev_definition():
    """Предыдущее определение."""
    global current_index
    if definitions:
        current_index = (current_index - 1) % len(definitions)
        label.config(text=definitions[current_index])
        if auto_update:
            stop_timers()
            time_left = interval_minutes * 60
            start_timers()

def get_random_index():
    """Возвращает случайный индекс, отличный от текущего (если возможно)."""
    if len(definitions) <= 1:
        return 0
    indices = list(range(len(definitions)))
    indices.remove(current_index)  # Убираем текущий
    return random.choice(indices)

def play_definitions():
    """Возобновление автоматической смены."""
    global auto_update
    auto_update = True
    if definitions and update_id is None:
        start_timers()


def pause_definitions():
    """Пауза автоматической смены."""
    global auto_update
    auto_update = False
    stop_timers()
    timer_label.config(text="Таймер на паузе")


def set_interval(minutes):
    """Установка интервала смены определений."""
    global interval_minutes, interval_ms, time_left
    interval_minutes = minutes
    interval_ms = minutes * 60 * 1000
    time_left = minutes * 60
    update_tray_menu()
    update_interval_buttons()
    if auto_update and definitions:
        stop_timers()
        start_timers()


def toggle_topmost():
    """Переключение режима 'поверх всех окон'."""
    global topmost_enabled
    topmost_enabled = not topmost_enabled
    root.attributes('-topmost', topmost_enabled)
    update_tray_menu()


def update_interval_buttons():
    """Подсветка активной кнопки интервала."""
    for minutes, btn in interval_buttons.items():
        btn.config(bg=ACTIVE_BG if interval_minutes == minutes else DARK_BG)


def show_window():
    """Показать главное окно."""
    root.deiconify()


def hide_window():
    """Скрыть главное окно в трей."""
    root.withdraw()


def quit_app():
    """Завершение приложения."""
    global tray_icon
    stop_timers()
    if tray_icon:
        tray_icon.stop()
    # Удаляем временную папку
    try:
        shutil.rmtree(TEMP_DIR)
    except Exception as e:
        print(f"Не удалось удалить временную папку: {e}")
    root.quit()
    root.destroy()


def get_status(item=None):
    """Функция для отображения статуса в трее."""
    return (
        f"Статус:\n"
        f"Интервал, мин: {interval_minutes} мин\n"
        f"Поверх окон: {'Вкл' if topmost_enabled else 'Выкл'}"
    )


def update_tray_menu():
    """Обновление контекстного меню в трее."""
    global tray_icon
    if tray_icon:
        tray_icon.menu = create_tray_menu()
        tray_icon.update_menu()


def create_tray_menu():
    """Создание меню в системном трее."""
    return pystray.Menu(
        item(get_status, lambda icon, item: None, enabled=False),
        item('Показать окно', lambda icon, item: show_window()),
        item('Поверх всех окон', lambda icon, item: toggle_topmost(), checked=lambda item: topmost_enabled),
        item('Выход', lambda icon, item: quit_app())
    )


def create_tray_icon():
    """Создание иконки в системном трее с пользовательским .ico файлом."""
    try:
        icon_path = resource_path('ico/icon.ico')
        image = Image.open(icon_path)
    except Exception as e:
        print(f"Не удалось загрузить иконку для трея: {e}. Используется стандартная.")
        image = Image.new('RGB', (64, 64), color=(73, 109, 137))
    
    return pystray.Icon(
        name='definition_app',
        icon=image,
        title='Определения',
        menu=create_tray_menu()
    )


def create_gui_controls():
    """Создание элементов управления."""
    global control_frame, interval_buttons, timer_label
    control_frame = tk.Frame(root, bg=DARK_BG)

    # Кнопки
    tk.Button(control_frame, text="📄 Загрузить файл", command=load_files, bg=DARK_BG, fg=LIGHT_TEXT).pack(side='left', padx=5)
    tk.Button(control_frame, text="📁 Загрузить папку", command=load_folder, bg=DARK_BG, fg=LIGHT_TEXT).pack(side='left', padx=5)
    tk.Button(control_frame, text="⬅️ Предыдущее", command=prev_definition, bg=DARK_BG, fg=LIGHT_TEXT).pack(side='left', padx=5)
    tk.Button(control_frame, text="➡️ Следующее", command=next_definition, bg=DARK_BG, fg=LIGHT_TEXT).pack(side='left', padx=5)

    # Интервал, мин:
    tk.Label(control_frame, text="Интервал, мин:", bg=DARK_BG, fg=LIGHT_TEXT).pack(side='left', padx=5)
    interval_buttons = {
        1: tk.Button(control_frame, text="1", command=lambda: set_interval(1), bg=DARK_BG, fg=LIGHT_TEXT),
        5: tk.Button(control_frame, text="5", command=lambda: set_interval(5), bg=DARK_BG, fg=LIGHT_TEXT),
        10: tk.Button(control_frame, text="10", command=lambda: set_interval(10), bg=DARK_BG, fg=LIGHT_TEXT)
    }
    for btn in interval_buttons.values():
        btn.pack(side='left', padx=2)

    # Скрыть окно
    tk.Button(control_frame, text="🙈", command=hide_window, bg=DARK_BG, fg=LIGHT_TEXT).pack(side='left', padx=5)

    # Таймер (без префикса)
    timer_label = tk.Label(control_frame, text="", bg=DARK_BG, fg=LIGHT_TEXT, font=("Arial", 10))
    timer_label.pack(side='right', padx=5, pady=5)

    update_interval_buttons()



def main():
    """Главная функция запуска приложения."""
    global root, label, tray_icon

    root = tk.Tk()
    root.title("Определения")
    root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
    root.configure(bg=DARK_BG)
    root.attributes('-topmost', topmost_enabled)

    # Установка иконки окна
    try:
        icon_path = resource_path('ico/icon.ico')
        root.iconbitmap(icon_path)
    except Exception as e:
        print(f"Ошибка иконки окна: {e}")

    # Применение тёмного заголовка (Windows 10/11)
    root.update()  # Важно: окно должно быть отрисовано
    set_dark_title_bar(root)

    # Текст определения
    label = tk.Label(
        root,
        text="Загрузка...",
        wraplength=WINDOW_WIDTH - 20,
        justify="left",
        bg=DARK_BG,
        fg=LIGHT_TEXT,
        font=("Arial", FONT_SIZE)
    )
    label.pack(expand=True, fill='both', padx=10, pady=10)

    create_gui_controls()

    # Загрузка сохранённых данных
    if load_saved_data():
        update_gui_after_load()
    else:
        label.config(text="Определения не найдены. Загрузите файлы.")
        control_frame.pack(expand=False, fill='x')

    # --- Иконка в трее ---
    try:
        tray_icon = create_tray_icon()
        # Запуск в отдельном потоке
        threading.Thread(target=tray_icon.run, daemon=True).start()
    except Exception as e:
        print(f"Ошибка при запуске иконки в трее: {e}")

    # Закрытие окна — скрытие в трей
    root.protocol("WM_DELETE_WINDOW", hide_window)

    root.mainloop()


if __name__ == "__main__":
    main()