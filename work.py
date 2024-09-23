import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText
import aiohttp
import asyncio
import threading
from bs4 import BeautifulSoup
from PIL import Image, ImageTk
import io
import re
import os
import locale

# Set the locale to a default value
try:
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
except locale.Error:
    locale.setlocale(locale.LC_ALL, 'C')

import ttkbootstrap as ttkb
from ttkbootstrap.constants import *
from ttkbootstrap.toast import ToastNotification
from ttkbootstrap.scrolled import ScrolledFrame

# Define global variables
problematic_links = []
current_page = 0
num_threads = 1
num_links_to_fetch = 10
num_models_to_show = 9  # Default number of models to show
download_directory = ""  # Default download directory

SETTINGS_FILE = 'settings.txt'

# Initialize and start the asyncio event loop in a separate thread
loop = asyncio.new_event_loop()

def start_event_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

loop_thread = threading.Thread(target=start_event_loop, args=(loop,), daemon=True)
loop_thread.start()

def load_settings():
    global num_threads, num_links_to_fetch, num_models_to_show, download_directory
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as file:
            lines = file.readlines()
            if len(lines) >= 4:
                num_threads = int(lines[0].strip())
                num_links_to_fetch = int(lines[1].strip())
                num_models_to_show = int(lines[2].strip())
                download_directory = lines[3].strip()
            else:
                save_settings_to_file()  # Save default settings if file is incomplete

def save_settings_to_file():
    with open(SETTINGS_FILE, 'w') as file:
        file.write(f"{num_threads}\n")
        file.write(f"{num_links_to_fetch}\n")
        file.write(f"{num_models_to_show}\n")
        file.write(f"{download_directory}\n")

async def search_workshop(session, search_text):
    search_url = f"https://steamcommunity.com/workshop/ajaxfindworkshops/?searchText={search_text}"
    async with session.get(search_url) as response:
        if response.status == 200:
            data = await response.json()
            if data:
                appid = data[0]['appid']
                return appid
    return None

async def get_links_from_workshop(session, appid, search_term=None):
    links = []
    for page in range(1, (num_links_to_fetch // 30) + 2):  # 30 links per page
        browse_url = f"https://steamcommunity.com/workshop/browse/?appid={appid}&p={page}"
        if search_term:
            browse_url += f"&searchtext={search_term}"
        browse_url += "&childpublishedfileid=0&browsesort=textsearch&section=&actualsort=textsearch"

        async with session.get(browse_url) as response:
            if response.status == 200:
                page_source = await response.text()
                soup = BeautifulSoup(page_source, 'html.parser')
                workshop_div = soup.find('div', class_='workshopBrowseItems')
                if workshop_div:
                    links.extend([div.find('a')['href'] for div in workshop_div.find_all('div', attrs={'data-panel': True})])
                if len(links) >= num_links_to_fetch:
                    break
    return links[:num_links_to_fetch]

async def fetch_workshop_item_details(session, item_id):
    base_url = "https://steamcommunity.com/sharedfiles/filedetails/?id="
    url = base_url + item_id
    async with session.post(
        "https://api.ggntw.com/steam.request",
        json={"url": url},
        headers={
            "Content-Type": "application/json",
            "User-Agent": "insomnia/2023.5.8"
        }
    ) as response:
        if response.status == 200:
            response_data = await response.json()
            if 'url' in response_data:
                return response_data
    return None

async def download_workshop_item(session, download_url, item_name):
    global download_directory
    async with session.get(download_url) as response:
        if response.status == 200:
            # Remove or replace invalid characters
            item_name = re.sub(r'[<>:"/\\|?*]', '_', item_name)
            
            if '.' not in item_name:
                content_type = response.headers.get('Content-Type')
                if content_type == 'application/zip':
                    item_name += '.zip'
                elif content_type == 'application/octet-stream':
                    item_name += '.bin'
                else:
                    item_name += '.dat'
            
            # Determine the download path
            if download_directory:
                download_path = os.path.join(download_directory, item_name)
            else:
                # Set default to user's Downloads directory if not set
                download_path = os.path.join(os.path.expanduser("~"), "Downloads", item_name)
            
            # Ensure the download directory exists
            os.makedirs(os.path.dirname(download_path), exist_ok=True)

            try:
                with open(download_path, 'wb') as file:
                    file.write(await response.read())
                show_toast("Success", f"Downloaded {item_name} successfully!")
            except PermissionError:
                show_toast("Error", f"Permission denied: Cannot write to {download_path}.", icon="error")
            except Exception as e:
                show_toast("Error", f"Failed to save {item_name}: {e}", icon="error")
        else:
            show_toast("Error", f"Failed to download {item_name}", icon="error")

def download_button_clicked(url, name):
    asyncio.run_coroutine_threadsafe(download_workshop_item_wrapper(url, name), loop)

async def download_workshop_item_wrapper(url, name):
    async with aiohttp.ClientSession() as session:
        await download_workshop_item(session, url, name)

def show_detailed_view(page=0):
    global current_page, num_models_to_show
    current_page = page
    selected_items = listbox_links.get(0, tk.END)
    if not selected_items:
        show_toast("Warning", "Please select an item.", icon="warning")
        return

    start_idx = page * num_models_to_show
    end_idx = min(start_idx + num_models_to_show, len(selected_items))

    detailed_view_window = ttkb.Toplevel(root)
    detailed_view_window.title("Detailed View")
    detailed_view_window.geometry("800x600")

    # Create a styled canvas with scrollbar
    scrolled_frame = ScrolledFrame(detailed_view_window)
    scrolled_frame.pack(fill=BOTH, expand=YES, padx=10, pady=10)

    async def fetch_details():
        async with aiohttp.ClientSession() as session:
            tasks = []
            for idx in range(start_idx, end_idx):
                selected_link = selected_items[idx]
                try:
                    item_id = fetch_workshop_item_id(selected_link)
                    tasks.append(fetch_workshop_item_details(session, item_id))
                except ValueError as ve:
                    show_toast("Error", f"Invalid URL format: {selected_link}\n{ve}", icon="error")
                    return

            item_details_list = await asyncio.gather(*tasks)

            row = 0
            col = 0
            for item_details in item_details_list:
                if not item_details:
                    show_toast("Error", "Failed to fetch item details.", icon="error")
                    return

                frame = ttkb.Frame(scrolled_frame, bootstyle="light")
                frame.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")

                # Load and display image
                img_url = item_details.get('image')
                if img_url:
                    try:
                        async with session.get(img_url) as img_response:
                            if img_response.status == 200:
                                img_data = io.BytesIO(await img_response.read())
                                img = Image.open(img_data)
                                img = img.resize((150, 150), Image.LANCZOS)
                                img = ImageTk.PhotoImage(img)

                                img_label = ttkb.Label(frame, image=img)
                                img_label.image = img
                                img_label.pack(pady=10)
                            else:
                                no_img_label = ttkb.Label(frame, text="No Image Available")
                                no_img_label.pack(pady=10)
                    except Exception:
                        no_img_label = ttkb.Label(frame, text="No Image Available")
                        no_img_label.pack(pady=10)

                # Display item details
                name_label = ttkb.Label(frame, text=item_details.get('name', 'Unknown Name'), wraplength=250, font=('Helvetica', 12, 'bold'))
                name_label.pack(pady=5)

                size_label = ttkb.Label(frame, text=f"Size: {item_details.get('size', 'Unknown Size')}", font=('Helvetica', 10))
                size_label.pack(pady=2)

                update_label = ttkb.Label(frame, text=f"Updated: {item_details.get('update', 'Unknown Update')}", font=('Helvetica', 10))
                update_label.pack(pady=2)

                # Download button with styling
                download_button = ttkb.Button(
                    frame, 
                    text="Download", 
                    command=lambda url=item_details['url'], name=item_details.get('name', 'unknown_item'): download_button_clicked(url, name),
                    bootstyle="success-outline"
                )
                download_button.pack(pady=10)

                col += 1
                if col > 2:
                    col = 0
                    row += 1

            # Navigation buttons
            nav_frame = ttkb.Frame(scrolled_frame)
            nav_frame.grid(row=row+1, columnspan=3, pady=20)

            if start_idx > 0:
                prev_button = ttkb.Button(nav_frame, text="Previous Page", bootstyle="info-outline", command=lambda: previous_page(detailed_view_window))
                prev_button.pack(side="left", padx=10)

            if end_idx < len(selected_items):
                next_button = ttkb.Button(nav_frame, text="Next Page", bootstyle="info-outline", command=lambda: next_page(detailed_view_window))
                next_button.pack(side="right", padx=10)

    asyncio.run_coroutine_threadsafe(fetch_details(), loop)

def next_page(current_window):
    current_window.destroy()
    show_detailed_view(current_page + 1)

def previous_page(current_window):
    current_window.destroy()
    show_detailed_view(current_page - 1)

def start_search():
    search_text = entry_name.get()
    search_term = entry_keyword.get()
    if not search_text:
        show_toast("Warning", "Please enter a name!", icon="warning")
        return

    async def search_and_fetch():
        async with aiohttp.ClientSession() as session:
            appid = await search_workshop(session, search_text)
            if appid:
                links = await get_links_from_workshop(session, appid, search_term)
                listbox_links.delete(0, tk.END)  # Clear previous search results
                if links:
                    for link in links:
                        listbox_links.insert(tk.END, link)
                    label_results.config(text=f"Found links: {len(links)}")
                    check_button.pack(pady=10)  # Show the "Check" button
                else:
                    show_toast("Info", "No links found.")
                    label_results.config(text="Found links:")
            else:
                show_toast("Info", "APPID not found")

    asyncio.run_coroutine_threadsafe(search_and_fetch(), loop)

def fetch_workshop_item_id(url):
    pattern = re.compile(r"[0-9]{2,15}")
    match = pattern.search(url)
    if match:
        return match.group(0)
    else:
        raise ValueError("CANNOT GET ID!")

def check_links():
    global problematic_links
    problematic_links.clear()
    global num_threads
    
    async def check_and_update():
        async with aiohttp.ClientSession() as session:
            tasks = []
            for link in listbox_links.get(0, tk.END):
                try:
                    item_id = fetch_workshop_item_id(link)
                    tasks.append(check_link(session, item_id))
                except ValueError:
                    problematic_links.append(link)

            results = await asyncio.gather(*tasks)

            for link, result in zip(listbox_links.get(0, tk.END), results):
                if not result:
                    problematic_links.append(link)

        # Remove problematic links from the listbox
        for link in problematic_links:
            try:
                index = listbox_links.get(0, tk.END).index(link)
                listbox_links.delete(index)
            except ValueError:
                continue
        
        label_results.config(text=f"Found links: {listbox_links.size()}")
        show_toast("Check Complete", "Problematic links have been removed.")

    asyncio.run_coroutine_threadsafe(check_and_update(), loop)

async def check_link(session, item_id):
    item_details = await fetch_workshop_item_details(session, item_id)
    if not item_details:
        return False
    return True

def show_problematic_links():
    if not problematic_links:
        show_toast("No Problems", "No problematic links found.")
        return

    problematic_window = ttkb.Toplevel(root)
    problematic_window.title("Problematic Links")
    problematic_window.geometry("600x400")

    text_widget = ScrolledText(problematic_window, width=80, height=20, wrap=tk.WORD, font=('Helvetica', 10))
    text_widget.pack(pady=20, padx=20, fill=BOTH, expand=YES)

    for link in problematic_links:
        text_widget.insert(tk.END, f"{link}\n")
    
    text_widget.config(state=tk.DISABLED)  # Disable editing

def show_settings():
    settings_window = ttkb.Toplevel(root)
    settings_window.title("Settings")
    settings_window.geometry("400x400")

    # Threads setting
    label_threads = ttkb.Label(settings_window, text="Number of Threads:")
    label_threads.pack(pady=(20,5))
    entry_threads = ttkb.Entry(settings_window)
    entry_threads.pack(pady=5)
    entry_threads.insert(0, str(num_threads))

    # Links to fetch setting
    label_links_to_fetch = ttkb.Label(settings_window, text="Number of Links to Fetch:")
    label_links_to_fetch.pack(pady=(20,5))
    entry_links_to_fetch = ttkb.Entry(settings_window)
    entry_links_to_fetch.pack(pady=5)
    entry_links_to_fetch.insert(0, str(num_links_to_fetch))
    
    # Models to show setting
    label_models_to_show = ttkb.Label(settings_window, text="Number of Models to Show:")
    label_models_to_show.pack(pady=(20,5))
    entry_models_to_show = ttkb.Entry(settings_window)
    entry_models_to_show.pack(pady=5)
    entry_models_to_show.insert(0, str(num_models_to_show))

    # Download directory setting
    label_download_directory = ttkb.Label(settings_window, text="Download Directory:")
    label_download_directory.pack(pady=(20,5))
    entry_download_directory = ttkb.Entry(settings_window, width=40)
    entry_download_directory.pack(pady=5)
    entry_download_directory.insert(0, download_directory)

    def browse_directory():
        selected_directory = filedialog.askdirectory()
        if selected_directory:
            entry_download_directory.delete(0, tk.END)
            entry_download_directory.insert(0, selected_directory)

    browse_button = ttkb.Button(settings_window, text="Browse", command=browse_directory, bootstyle="info-outline")
    browse_button.pack(pady=10)

    def save_settings():
        global num_threads, num_links_to_fetch, num_models_to_show, download_directory
        try:
            num_threads_new = int(entry_threads.get())
            num_links_to_fetch_new = int(entry_links_to_fetch.get())
            num_models_to_show_new = int(entry_models_to_show.get())
            download_directory_new = entry_download_directory.get().strip()

            if download_directory_new:
                if not os.path.isdir(download_directory_new):
                    os.makedirs(download_directory_new, exist_ok=True)

            num_threads = num_threads_new
            num_links_to_fetch = num_links_to_fetch_new
            num_models_to_show = num_models_to_show_new
            download_directory = download_directory_new

            save_settings_to_file()
            show_toast("Success", "Settings saved successfully.")
            settings_window.destroy()
        except ValueError:
            show_toast("Error", "Invalid values. Please enter numeric values where required.", icon="error")
        except Exception as e:
            show_toast("Error", f"An error occurred while saving settings: {e}", icon="error")

    save_button = ttkb.Button(settings_window, text="Save", command=save_settings, bootstyle="success")
    save_button.pack(pady=20)

# Initialize main application window
root = ttkb.Window(themename="darkly")
root.title("Steam Workshop")
root.geometry("1000x600")

# Load settings on startup
load_settings()

# Create the sidebar menu frame
sidebar = ttkb.Frame(root, bootstyle="dark")
sidebar.pack(expand=False, fill='y', side='left', anchor='nw')

# Sidebar menu label
sidebar_label = ttkb.Label(sidebar, text="Steam Workshop", font=('Helvetica', 16, 'bold'), bootstyle="inverse-dark")
sidebar_label.pack(pady=30)

# Start button in the sidebar
start_button = ttkb.Button(sidebar, text="Start", command=start_search, bootstyle="success")
start_button.pack(pady=10, fill='x', padx=10)

# Detailed view button in the sidebar
details_button = ttkb.Button(sidebar, text="Detailed View", command=show_detailed_view, bootstyle="info")
details_button.pack(pady=10, fill='x', padx=10)

# Problematic links button in the sidebar
problematic_button = ttkb.Button(sidebar, text="Problematic Links", command=show_problematic_links, bootstyle="warning")
problematic_button.pack(pady=10, fill='x', padx=10)

# Settings button in the sidebar
settings_button = ttkb.Button(sidebar, text="Settings", command=show_settings, bootstyle="secondary")
settings_button.pack(pady=10, fill='x', padx=10)

# Create the main content frame
main_content = ttkb.Frame(root)
main_content.pack(expand=True, fill='both', side='right', padx=20, pady=20)

# Entry for "Enter Name"
label_name = ttkb.Label(main_content, text="Enter Name:")
label_name.pack(pady=(10,5), anchor='w')
entry_name = ttkb.Entry(main_content, width=50)
entry_name.pack(pady=5, fill='x')

# Entry for "Enter Keyword (optional):"
label_keyword = ttkb.Label(main_content, text="Enter Keyword (optional):")
label_keyword.pack(pady=(20,5), anchor='w')
entry_keyword = ttkb.Entry(main_content, width=50)
entry_keyword.pack(pady=5, fill='x')

# Listbox to display the results
label_results = ttkb.Label(main_content, text="Found Links:")
label_results.pack(pady=(20,5), anchor='w')
listbox_links = tk.Listbox(main_content, width=100, height=15, font=('Helvetica', 10), bg='#2c3e50', fg='white')
listbox_links.pack(pady=5, fill='both', expand=True)

# Add "Check" button with styling
check_button = ttkb.Button(main_content, text="Check", command=check_links, bootstyle="primary")
check_button.pack(pady=10)

def show_toast(title, message, duration=3000, icon="info"):
    toast = ToastNotification(
        title=title,
        message=message,
        duration=duration,
        bootstyle=icon
    )
    toast.show_toast()

# Run the application
root.mainloop()
