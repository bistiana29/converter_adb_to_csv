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

# CSS
st.markdown("""
    <style>
    /* Mengatur tag multiselect agar tingginya menyesuaikan teks */
    .stMultiSelect div[data-baseweb="select"] span[data-baseweb="tag"] {
        max-width: 100% !important;
        height: auto !important;
        min-height: 2rem;
        padding-top: 5px;
        padding-bottom: 5px;
    }
    /* Memaksa teks di dalam tag untuk turun ke baris baru (word-wrap) */
    .stMultiSelect div[data-baseweb="select"] span[data-baseweb="tag"] span[title] {
        white-space: normal !important;
        word-break: break-all !important;
        overflow: visible !important;
        text-overflow: clip !important;
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

# FUNGSI CORE: SSH & EKSTRAKSI
def connect_and_search(ip, username, password, directory):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=ip, 
            username=username, 
            password=password, 
            timeout=10,
            look_for_keys=False,
            allow_agent=False
        )
        
        # Cari file .adb
        stdin, stdout, stderr = ssh.exec_command(f'find {directory} -type f -name "*.adb"')
        files = stdout.read().decode().splitlines()
        
        st.session_state.ssh_client = ssh
        return files
    except Exception as e:
        st.error(f"Gagal terhubung atau mencari file: {e}")
        return []

def extract_tags(data):
    # Batasi pencarian hanya pada 10.000 byte pertama (Area Header)
    header_data = data[:10000] 
    text = header_data.decode(errors='ignore')

    tags = []
    seen = set() # Sekarang seen akan menyimpan nama tag TANPA awalan '#'

    hash_tags = re.findall(r'#[A-Za-z0-9_]{3,}', text)
    normal_tags = re.findall(r'[A-Za-z0-9]+(?:[_:][A-Za-z0-9]+)+', text)

    # Karena hash_tags digabungkan duluan, tag dengan '#' akan diproses lebih awal
    for c in hash_tags + normal_tags:
        if len(c) > 5:
            # Hilangkan tanda '#' hanya untuk keperluan pengecekan duplikat
            base_name = c.lstrip('#')
            
            # Jika nama dasarnya belum pernah dilihat, masukkan ke list
            if base_name not in seen:
                seen.add(base_name)
                tags.append(c)

    return tags

def extract_adb_from_bytes(data):
    tags = extract_tags(data)
    if not tags:
        return pd.DataFrame()

    num_tags = len(tags)
    SLOT_SIZE = 48
    GROUP_SIZE = num_tags * SLOT_SIZE
    MAX_TIME_GAP = 600

    anchor = None
    offset = 0

    # 1. FIND ANCHOR (DINAMIS)
    while offset + GROUP_SIZE <= len(data):
        try:
            group_ts = []
            for i in range(num_tags):
                ts = struct.unpack_from('<I', data, offset + (i * SLOT_SIZE))[0]
                group_ts.append(ts)
            
            # Cek apakah blok data sinkron (jarak waktu antar tag masuk akal)
            if max(group_ts) - min(group_ts) <= MAX_TIME_GAP:
                dt = datetime.datetime.fromtimestamp(group_ts[0], datetime.timezone.utc)
                if 2000 < dt.year < 2050: 
                    anchor = offset
                    print(f"Anchor found at offset {anchor}, first timestamp: {dt}, total tags: {num_tags}")
                    break
        except:
            pass
        offset += 4

    if anchor is None:
        print("Could not find anchor point!")
        return pd.DataFrame()

    # 2. SEQUENCE EXTRACTION
    rows = []
    offset = anchor

    while offset + GROUP_SIZE <= len(data):
        try:
            group_ts = []
            for i in range(num_tags):
                slot = offset + (i * SLOT_SIZE)
                ts = struct.unpack_from('<I', data, slot)[0]
                group_ts.append(ts)

            diff = max(group_ts) - min(group_ts)
            
            if diff <= MAX_TIME_GAP:
                dt_check = datetime.datetime.fromtimestamp(group_ts[0], datetime.timezone.utc)
                
                if 2000 < dt_check.year < 2050:
                    for i, tag in enumerate(tags):
                        slot = offset + (i * SLOT_SIZE)
                        ts = group_ts[i]
                        value = struct.unpack_from('<d', data, slot + 16)[0]
                        status = struct.unpack_from('<I', data, slot + 24)[0]
                        
                        # Normalisasi Quality
                        if status != 0:
                            status = -1

                        dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
                        ts_str = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
                        
                        rows.append([slot, ts_str, tag, round(value, 6), status])
                    
                    offset += GROUP_SIZE
                    continue 

            offset += 4

        except Exception:
            offset += 4

    df = pd.DataFrame(rows, columns=["ID", "Timestamp", "TagName", "Value", "Quality"])
    return df

# --- UI: SIDEBAR ---
with st.sidebar:
    st.markdown("### Koneksi Target")
    ip_address = st.text_input("IP Address Target", "x.x.x.x")
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
                total_tag = master_df['TagName'].nunique()
                st.session_state.extracted_data = master_df
                st.success(f"✅ Berhasil menggabungkan {len(selected_files)} file dengan total {len(master_df)} baris data dan {total_tag} tag!")
            else:
                st.error("Gagal mengekstrak data. Format file mungkin tidak sesuai atau kosong.")
                
        except Exception as e:
            st.error(f"Terjadi kesalahan saat ekstraksi: {e}")

st.divider()

# 3. Filter Waktu & Unduh
st.markdown("### Filter Waktu (UTC) & Unduh")

if not st.session_state.extracted_data.empty:
    df = st.session_state.extracted_data.copy()
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
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

    st.divider()
    st.markdown("**Filter TagName**")
    unique_tags = df['TagName'].unique().tolist()
    
    num_cols = min(len(unique_tags), 5)
    num_cols = max(1, num_cols)
    
    cols = st.columns(num_cols)
    selected_tags = []
    
    for i, tag in enumerate(unique_tags):
        col_index = i % num_cols
        with cols[col_index]:
            if st.checkbox(tag, value=True, key=f"tag_{tag}"):
                selected_tags.append(tag)

    # Filter Data (Perhatikan tambahan kondisi TagName di ujung mask)
    mask = (
        (df['Timestamp_tz'] >= start_dt) & 
        (df['Timestamp_tz'] <= end_dt) & 
        (df['TagName'].isin(selected_tags))
    )
    
    filtered_df = df.loc[mask].drop(columns=['Timestamp_tz'])
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
