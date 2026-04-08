import streamlit as st
import paramiko
import pandas as pd
import struct
import datetime
import re
import io

# Konfigurasi Halaman
st.set_page_config(
    page_title="ADB Extractor", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# CSS khusus untuk mencegah teks terpotong (...) pada chip multiselect
st.markdown("""
    <style>
    div[data-baseweb="select"] span[data-baseweb="tag"] {
        max-width: none !important;
    }
    div[data-baseweb="select"] span[data-baseweb="tag"] span {
        white-space: normal !important;
        word-break: break-all !important;
    }
    </style>
""", unsafe_allow_html=True)

# Inisialisasi Session State
if 'remote_files' not in st.session_state:
    st.session_state.remote_files = []
if 'extracted_data' not in st.session_state:
    st.session_state.extracted_data = pd.DataFrame()
if 'ssh_client' not in st.session_state:
    st.session_state.ssh_client = None

# --- FUNGSI CORE: SSH & EKSTRAKSI ---
def connect_and_search(ip, username, password, directory):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Jika password kosong, gunakan None agar mencoba metode autentikasi lain jika ada
        ssh.connect(hostname=ip, username=username, password=password if password else None, timeout=10)
        
        # Cari file .adb
        stdin, stdout, stderr = ssh.exec_command(f'find {directory} -type f -name "*.adb"')
        files = stdout.read().decode().splitlines()
        
        st.session_state.ssh_client = ssh
        return files
    except Exception as e:
        st.error(f"Gagal terhubung atau mencari file: {e}")
        return []

def extract_adb_from_bytes(data):
    tags = [m.group().decode() for m in re.finditer(b'#[A-Z_0-9]{3,}', data)]
    if not tags:
        return pd.DataFrame()

    anchor = None
    offset = 0
    num_tags = len(tags)
    SLOT_SIZE = 48
    GROUP_SIZE = num_tags * SLOT_SIZE
    MAX_TIME_GAP = 150 

    # Cari Anchor
    while offset + GROUP_SIZE <= len(data):
        try:
            group_ts = [struct.unpack_from('<I', data, offset + (i * SLOT_SIZE))[0] for i in range(num_tags)]
            if max(group_ts) - min(group_ts) <= MAX_TIME_GAP:
                dt = datetime.datetime.fromtimestamp(group_ts[0], datetime.timezone.utc)
                if dt.year > 2000: 
                    anchor = offset
                    break
        except:
            pass
        offset += 4

    if anchor is None:
        return pd.DataFrame()

    rows = []
    offset = anchor

    # Ekstrak Data
    while offset + GROUP_SIZE <= len(data):
        try:
            group_ts = [struct.unpack_from('<I', data, offset + (i * SLOT_SIZE))[0] for i in range(num_tags)]
            diff = max(group_ts) - min(group_ts)
            
            if diff <= MAX_TIME_GAP:
                dt_check = datetime.datetime.fromtimestamp(group_ts[0], datetime.timezone.utc)
                if dt_check.year > 2000:
                    for i, tag in enumerate(tags):
                        slot = offset + (i * SLOT_SIZE)
                        value = struct.unpack_from('<d', data, slot + 16)[0]
                        status = struct.unpack_from('<I', data, slot + 24)[0]
                        dt = datetime.datetime.fromtimestamp(group_ts[i], datetime.timezone.utc)
                        
                        # Hanya format kolom yang direquest
                        rows.append([slot, dt, tag, round(value, 6), status])
                    offset += GROUP_SIZE
                    continue 
            offset += 4
        except Exception:
            offset += 4

    # DataFrame direvisi sesuai format yang diminta
    df = pd.DataFrame(rows, columns=["ID", "Timestamp", "TagName", "Value", "Quality"])
    return df

# --- UI: SIDEBAR ---
with st.sidebar:
    st.markdown("### Koneksi Target")
    ip_address = st.text_input("IP Address Target", "192.168.1.40")
    username = st.text_input("Username", "root")
    password = st.text_input("Password (Opsional)", type="password")
    directory = st.text_input("Direktori Pencarian", "/")
    
    if st.button("Cari File", type="primary"):
        with st.spinner("Mencari file di perangkat target..."):
            files = connect_and_search(ip_address, username, password, directory)
            if files:
                st.session_state.remote_files = files
                st.success(f"Ditemukan {len(files)} file .adb!")
            else:
                st.session_state.remote_files = []
                st.warning("Tidak ada file .adb yang ditemukan.")

# --- UI: MAIN AREA ---
st.title("ADB to CSV Converter")
st.write("Ekstrak dan gabungkan log dari perangkat secara remote.")

# 2. Pilih File Ekstraksi
st.markdown("### Pilih File Ekstraksi")
st.info("💡 Anda bisa memilih lebih dari satu file. Data akan otomatis digabungkan.")

selected_files = st.multiselect(
    "Daftar file .adb di target:",
    options=st.session_state.remote_files,
    default=st.session_state.remote_files if len(st.session_state.remote_files) == 1 else []
)

if st.button("Preview"):
    if not selected_files:
        st.warning("Pilih minimal 1 file untuk diekstrak.")
    elif not st.session_state.ssh_client:
        st.error("Koneksi SSH terputus. Silakan cari file kembali.")
    else:
        all_dfs = []
        progress_bar = st.progress(0)
        
        try:
            sftp = st.session_state.ssh_client.open_sftp()
            
            for i, file_path in enumerate(selected_files):
                # Baca file dari remote ke memory
                file_obj = io.BytesIO()
                sftp.getfo(file_path, file_obj)
                file_obj.seek(0)
                file_bytes = file_obj.read()
                
                # Proses data
                df = extract_adb_from_bytes(file_bytes)
                if not df.empty:
                    all_dfs.append(df)
                
                progress_bar.progress((i + 1) / len(selected_files))
                
            sftp.close()
            
            if all_dfs:
                master_df = pd.concat(all_dfs, ignore_index=True)
                master_df = master_df.sort_values('Timestamp').reset_index(drop=True)
                st.session_state.extracted_data = master_df
                st.success(f"✅ Berhasil menggabungkan {len(selected_files)} file dengan total {len(master_df)} baris data!")
            else:
                st.error("Gagal mengekstrak data. Format file mungkin tidak sesuai atau kosong.")
                
        except Exception as e:
            st.error(f"Terjadi kesalahan saat ekstraksi: {e}")

st.divider()

# 3. Filter Waktu & Unduh
st.markdown("### Filter Waktu (UTC) & Unduh")

if not st.session_state.extracted_data.empty:
    df = st.session_state.extracted_data.copy()
    
    min_time = df['Timestamp'].min().to_pydatetime()
    max_time = df['Timestamp'].max().to_pydatetime()
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**Start Time**")
        start_date = st.date_input("Start Date", min_time.date())
        h1, m1 = st.columns(2)
        with h1:
            start_hour = st.selectbox("Hour", options=list(range(24)), index=min_time.hour, format_func=lambda x: f"{x:02d}", key="sh")
        with m1:
            start_min = st.selectbox("Min", options=list(range(60)), index=min_time.minute, format_func=lambda x: f"{x:02d}", key="sm")
            
    with col2:
        st.markdown("**End Time**")
        end_date = st.date_input("End Date", max_time.date())
        h2, m2 = st.columns(2)
        with h2:
            end_hour = st.selectbox("Hour", options=list(range(24)), index=max_time.hour, format_func=lambda x: f"{x:02d}", key="eh")
        with m2:
            end_min = st.selectbox("Min", options=list(range(60)), index=max_time.minute, format_func=lambda x: f"{x:02d}", key="em")
        
    start_dt = pd.to_datetime(f"{start_date} {start_hour:02d}:{start_min:02d}:00")
    end_dt = pd.to_datetime(f"{end_date} {end_hour:02d}:{end_min:02d}:59")
    
    if start_dt.tzinfo is None: start_dt = start_dt.tz_localize('UTC')
    if end_dt.tzinfo is None: end_dt = end_dt.tz_localize('UTC')
    df['Timestamp_tz'] = df['Timestamp'].dt.tz_localize('UTC') if df['Timestamp'].dt.tz is None else df['Timestamp']

    # Filter Data
    mask = (df['Timestamp_tz'] >= start_dt) & (df['Timestamp_tz'] <= end_dt)
    filtered_df = df.loc[mask].drop(columns=['Timestamp_tz'])
    
    # Format string ISO 8601
    filtered_df['Timestamp'] = filtered_df['Timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    st.markdown(f"**Total Data Setelah Difilter:** {len(filtered_df)} baris")
    
    st.dataframe(filtered_df, use_container_width=True, hide_index=True)
    
    csv = filtered_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download CSV",
        data=csv,
        file_name='exported_adb_data.csv',
        mime='text/csv',
        type="primary"
    )
else:
    st.info("Lakukan ekstraksi file terlebih dahulu untuk melihat dan mengunduh data.")
