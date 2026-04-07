import streamlit as st
import struct
import datetime
import re
import pandas as pd
import io

# Konfigurasi Halaman
st.set_page_config(page_title="ADB to CSV Converter", layout="wide", page_icon="⚙️")

st.title("EdgeLink .adb to CSV Extractor & Filter")
st.write("Unggah file log `.adb`, filter berdasarkan rentang waktu spesifik, dan unduh hasilnya dalam format CSV.")

# Fungsi parsing
@st.cache_data
def parse_adb(file_bytes):
    data = file_bytes
    
    tags = [m.group().decode() for m in re.finditer(b'#[A-Z_0-9]{3,}', data)]
    if not tags:
        return None, "Tidak ada tag yang ditemukan di dalam file."

    anchor = None
    offset = 0
    num_tags = len(tags)
    SLOT_SIZE = 48
    GROUP_SIZE = num_tags * SLOT_SIZE
    MAX_TIME_GAP = 150 

    while offset + GROUP_SIZE <= len(data):
        try:
            group_ts = []
            for i in range(num_tags):
                ts = struct.unpack_from('<I', data, offset + (i * SLOT_SIZE))[0]
                group_ts.append(ts)
            
            if max(group_ts) - min(group_ts) <= MAX_TIME_GAP:
                dt = datetime.datetime.fromtimestamp(group_ts[0], datetime.timezone.utc)
                if dt.year > 2000: 
                    anchor = offset
                    break
        except:
            pass
        offset += 4

    if anchor is None:
        return None, "Tidak dapat menemukan titik anchor yang valid."

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
                if dt_check.year > 2000:
                    for i, tag in enumerate(tags):
                        slot = offset + (i * SLOT_SIZE)
                        ts = group_ts[i]
                        value = struct.unpack_from('<d', data, slot + 16)[0]
                        status = struct.unpack_from('<I', data, slot + 24)[0]
                        dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
                        
                        ts_str = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
                        rows.append([slot, ts_str, tag, round(value, 6), status])
                    
                    offset += GROUP_SIZE
                    continue 
            offset += 4

        except:
            offset += 4

    df = pd.DataFrame(rows, columns=["ID", "Timestamp", "TagName", "Value", "Quality"])
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])

    return df, f"Berhasil mengekstrak {len(df)} baris data dengan {len(tags)} tags: {', '.join(tags)}"

# --- UI ---
uploaded_file = st.file_uploader("Pilih file .adb", type=['adb'], accept_multiple_files=False, key="single_file_uploader")

if uploaded_file is not None:
    with st.spinner('Mengekstrak data biner...'):
        file_bytes = uploaded_file.read()
        df, message = parse_adb(file_bytes)

    if df is not None and not df.empty:
        st.success(message)

        st.subheader("Filter Waktu (UTC)")

        # ✅ FIX: definisikan min & max datetime
        min_dt = df['Timestamp'].min()
        max_dt = df['Timestamp'].max()

        # dropdown options
        hours = [f"{h:02d}" for h in range(24)]
        minutes = [f"{m:02d}" for m in range(0, 60, 1)]

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Start Time**")
            start_date = st.date_input("Start Date", min_dt.date())

            c1, c2 = st.columns(2)
            with c1:
                start_hour = st.selectbox("Hour", hours, index=int(min_dt.strftime("%H")), key="start_hour")
            with c2:
                start_minute = st.selectbox("Min", minutes, index=int(min_dt.strftime("%M")), key="start_minute")

        with col2:
            st.markdown("**End Time**")
            end_date = st.date_input("End Date", max_dt.date())

            c3, c4 = st.columns(2)
            with c3:
                end_hour = st.selectbox("Hour", hours, index=int(max_dt.strftime("%H")), key="end_hour")
            with c4:
                end_minute = st.selectbox("Min", minutes, index=int(max_dt.strftime("%M")), key="end_minute")

        # gabungkan datetime
        user_start_dt = pd.to_datetime(f"{start_date} {start_hour}:{start_minute}").tz_localize('UTC')
        user_end_dt = pd.to_datetime(f"{end_date} {end_hour}:{end_minute}").tz_localize('UTC')

        # filter
        mask = (df['Timestamp'] >= user_start_dt) & (df['Timestamp'] <= user_end_dt)
        filtered_df = df.loc[mask].copy()

        filtered_df = filtered_df.sort_values(by="Timestamp").reset_index(drop=True)

        st.markdown(f"**Total Data Setelah Difilter: `{len(filtered_df)}` baris**")

        filtered_df['Timestamp'] = filtered_df['Timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%SZ')

        st.dataframe(filtered_df.head(100), use_container_width=True)

        if len(filtered_df) > 100:
            st.caption(f"*Menampilkan 100 baris pertama dari total {len(filtered_df)} baris...*")

        csv = filtered_df.to_csv(index=False).encode('utf-8')

        download_filename = uploaded_file.name.replace('.adb', '_filtered.csv')

        st.download_button(
            label="⬇️ Download CSV",
            data=csv,
            file_name=download_filename,
            mime="text/csv",
            type="primary"
        )

    else:
        st.error("Data kosong atau gagal parsing.")