#!/usr/bin/env python3
"""
Exact Sample Video Generator - Matches production scripts exactly
Includes: first.mp4 (bismillah) + video + last.mp4 merging
Usage: py generate_sample_video_exact.py --lang en --surah 2 --ayah 14
"""

import argparse
import os
import sys
import time
import subprocess
import random
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image as PILImage
from mutagen.mp3 import MP3
from wand.image import Image
from wand.drawing import Drawing
from wand.color import Color

try:
    import moviepy.editor as mp
except ImportError:
    mp = None

# ============ CONFIGURATION ============
QURAN_API = "https://api.quran.com/api/v4"
AUDIO_API = "https://api.alquran.cloud/v1"
VIDEO_FPS = 30
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
POSTER_SUPERSAMPLE = 2
POSTER_RENDER_WIDTH = VIDEO_WIDTH * POSTER_SUPERSAMPLE
POSTER_RENDER_HEIGHT = VIDEO_HEIGHT * POSTER_SUPERSAMPLE
VIDEO_CRF = "15"
VIDEO_PRESET = "slower"
VIDEO_BITRATE = "12000k"
VIDEO_MAXRATE = "18000k"
VIDEO_BUFSIZE = "24000k"
AUDIO_BITRATE = "256k"
THUMBNAIL_INTRO_SECONDS = 0.2

# Language configs (exact from production)
LANG_CONFIG = {
    "en": {
        "name": "Learn English Quran Daily",
        "repo_path": "learnenglishqurandaily",
    },
    "ur": {
        "name": "Learn Urdu Quran Daily",
        "repo_path": "learnurduqurandaily",
    },
    "ar": {
        "name": "Learn Quran Daily",
        "repo_path": "learnqurandaily",
    }
}

AYAH_COUNTS = [7, 286, 200, 176, 120, 165, 206, 75, 129, 109, 123, 111, 43, 52, 99, 128, 111, 110, 98, 135,
               112, 78, 118, 64, 77, 227, 93, 88, 69, 60, 34, 30, 73, 54, 45, 83, 182, 88, 75, 85, 54, 53,
               89, 59, 37, 35, 38, 29, 18, 45, 60, 49, 62, 55, 78, 96, 29, 22, 24, 13, 14, 11, 11, 18, 12,
               12, 30, 52, 52, 44, 28, 28, 20, 56, 40, 37, 16, 30, 27, 25, 33, 26, 23, 20, 29, 29, 7, 5, 32,
               8, 38, 40, 29, 25, 32, 72, 28, 17, 72, 91, 10, 33, 34, 13, 33, 33, 28, 48, 29, 16, 47, 8, 9,
               27, 25, 43, 58, 11, 49, 34, 16, 33, 6]


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} - {level} - {msg}")


def fetch_with_retries(url, max_retries=3, delay=2):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt < max_retries - 1:
                log(f"Attempt {attempt + 1} failed: {e}. Retrying...")
                time.sleep(delay)
            else:
                log(f"Failed after {max_retries} attempts: {e}", "ERROR")
                return {}
    return {}


def fetch_quran_ayah(surah, ayah, lang="en"):
    """Exact API calls from production scripts"""
    try:
        # Arabic text
        arabic_url = f"{QURAN_API}/quran/verses/uthmani?verse_key={surah}:{ayah}"
        arabic_response = fetch_with_retries(arabic_url)
        arabic_text = arabic_response.get("verses", [{}])[0].get("text_uthmani", "No Arabic text found")

        # Surah metadata
        surah_meta_url = f"{QURAN_API}/chapters/{surah}"
        surah_meta_response = fetch_with_retries(surah_meta_url)
        surah_data = surah_meta_response.get("chapter", {})
        surah_name_simple = surah_data.get("name_simple", "Surah")
        surah_name_arabic = surah_data.get("name_arabic", "سورة")
        en_translated_surah_name = surah_data.get("translated_name", {}).get("name", "Chapter")

        # Translation
        if lang == "en":
            trans_url = f"{AUDIO_API}/surah/{surah}/en.sahih"
        elif lang == "ur":
            trans_url = f"{AUDIO_API}/surah/{surah}/ur.junagarhi"
        else:
            trans_url = ""

        trans_response = fetch_with_retries(trans_url) if trans_url else {}
        ayahs = trans_response.get("data", {}).get("ayahs", [])
        english_text = next((a.get("text") for a in ayahs if a.get("numberInSurah") == ayah), "No translation found")

        # Audio (Mishary al-Affasy)
        audio_url_response = fetch_with_retries(f"{AUDIO_API}/ayah/{surah}:{ayah}/ar.alafasy")
        audio_url = audio_url_response.get("data", {}).get("audio", "")

        # Append verse-end marker
        _WESTERN_TO_ARABIC_INDIC = str.maketrans('0123456789', '\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669')
        arabic_text = arabic_text + ' \uFD3F' + str(ayah).translate(_WESTERN_TO_ARABIC_INDIC) + '\uFD3E'

        return {
            "arabic": arabic_text,
            "english": english_text,
            "surah_name": surah_name_simple,
            "surah_name_arabic": surah_name_arabic,
            "en_name": en_translated_surah_name,
            "audio_url": audio_url,
        }
    except Exception as e:
        log(f"Error fetching Quran data: {e}", "ERROR")
        return {}


def download_audio(audio_url, output_file):
    if not audio_url:
        log(f"No audio URL provided", "ERROR")
        return False

    try:
        response = requests.get(audio_url, timeout=30)
        response.raise_for_status()
        with open(output_file, "wb") as f:
            f.write(response.content)
        log(f"✓ Audio downloaded: {output_file}")
        return True
    except Exception as e:
        log(f"Error downloading audio: {e}", "ERROR")
        return False


def clean_html(text):
    """Exact from production"""
    import re
    text = re.sub(r'<[^>]*>', '', text)
    text = re.sub(r'\[\d+\]', '', text)
    text = re.sub(r'foot_note=\d+', '', text)
    text = re.sub(r',\s*\d+', '.', text)
    text = re.sub(r'.\s*\d+', '.', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r',\s*$', '.', text).strip()
    if text:
        text = text[0].upper() + text[1:]
    return text


def wrap_text(draw, text, font, font_size, max_width):
    """Exact from production"""
    words = text.split()
    lines = []
    current_line = ""

    dummy_img = Image(width=1, height=1)
    draw.font = font
    draw.font_size = font_size

    for word in words:
        test_line = f"{current_line} {word}".strip()
        metrics = draw.get_font_metrics(dummy_img, test_line, True)

        if metrics.text_width < max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

    dummy_img.close()
    return lines


def get_cumulative_ayah_number(surah, ayah):
    """Exact from production"""
    total_ayahs = sum(AYAH_COUNTS)
    previous_ayahs = sum(AYAH_COUNTS[s] for s in range(0, surah - 1))
    current_ayah_position = previous_ayahs + ayah
    return current_ayah_position, total_ayahs


def scaled(value):
    return int(round(value * POSTER_SUPERSAMPLE))


def create_poster(surah, ayah, arabic, english, surah_name, surah_name_arabic, en_translated_surah_name, background_path, output_path, fonts_dir):
    """EXACT from production daily_quran.py"""
    try:
        poster_width, poster_height = POSTER_RENDER_WIDTH, POSTER_RENDER_HEIGHT

        img = Image(filename=background_path)
        img.depth = 32
        img.resize(poster_width, poster_height)
        draw = Drawing()

        # Font paths
        arabic_font = str(fonts_dir / "UthmanicHafs1v18p3.ttf")
        english_font = str(fonts_dir / "MONTSERRAT-BOLD.TTF")
        english_font_small = str(fonts_dir / "Montserrat-VariableFont_wght.ttf")

        # Calculate font size based on text width (exact from production)
        draw.font = arabic_font
        draw.font_size = scaled(55)
        arabic_text_width = draw.get_font_metrics(img, arabic, True).text_width

        if arabic_text_width > scaled(2000):
            arabic_font_size = scaled(40)
            english_font_size = scaled(20)
        elif scaled(1000) < arabic_text_width < scaled(2000):
            arabic_font_size = scaled(45)
            english_font_size = scaled(25)
        elif scaled(500) < arabic_text_width < scaled(1000):
            arabic_font_size = scaled(55)
            english_font_size = scaled(25)
        else:
            arabic_font_size = scaled(65)
            english_font_size = scaled(30)

        # ===== TITLE SECTION =====
        # Arabic Surah Name
        draw.font = arabic_font
        draw.font_style = 'italic'
        draw.font_size = scaled(75)
        draw.font_weight = 700
        draw.fill_color = Color("#FFFFFF")
        arabic_text_width = draw.get_font_metrics(img, surah_name_arabic, True).text_width
        arabic_x_position = (poster_width // 2) - (arabic_text_width // 2)
        draw.text(int(arabic_x_position), scaled(145), surah_name_arabic)

        # English Surah Name
        draw.font = english_font
        draw.font_size = scaled(25)
        draw.fill_color = Color("#FFFFFF")
        english_text_width = draw.get_font_metrics(img, f"{surah_name} ({en_translated_surah_name})", True).text_width
        english_x_position = (poster_width // 2) - (english_text_width // 2)
        draw.text(int(english_x_position), scaled(195), f"{surah_name} ({en_translated_surah_name})")

        # Verse reference (exact format from production)
        draw.font = english_font
        draw.font_size = scaled(25)
        draw.fill_color = Color("#FFFFFF")
        surah_text = f"(surah {surah} : {ayah} ayah)"
        surah_width = draw.get_font_metrics(img, surah_text, True).text_width
        surah_position = (poster_width // 2) - (surah_width // 2)
        draw.text(int(surah_position), scaled(225), surah_text)

        # ===== PROGRESS BAR =====
        current_ayah_position, total_ayahs = get_cumulative_ayah_number(surah, ayah)
        completed_percentage = round((current_ayah_position / total_ayahs) * 100, 2)
        progress_bar_width = int((current_ayah_position / total_ayahs) * scaled(900))

        bar_x, bar_y, bar_height = scaled(90), scaled(260), scaled(10)
        full_bar_width = scaled(900)

        # Bar background
        draw.fill_color = Color("rgba(166, 166, 166, 0.5)")
        draw.rectangle(left=bar_x, top=bar_y, width=full_bar_width, height=bar_height)

        # Bar progress
        draw.fill_color = Color("#FFFFFF")
        draw.rectangle(left=bar_x, top=bar_y, width=progress_bar_width, height=bar_height)

        # Progress percentage (exact format from production)
        progress_text = f"(Quran Completion: {completed_percentage}%)"
        draw.font = english_font_small
        draw.font_size = scaled(22)
        draw.fill_color = Color("#FFFFFF")
        text_width = draw.get_font_metrics(img, progress_text, True).text_width
        text_x_position = (poster_width // 2) - (text_width // 2)
        draw.text(int(text_x_position), bar_y + scaled(32), progress_text)

        # ===== ARABIC TEXT =====
        draw.font = arabic_font
        draw.font_style = 'italic'
        draw.font_size = arabic_font_size
        draw.font_weight = 500
        draw.fill_color = Color("#FFFFFF")

        arabic_lines = wrap_text(draw, arabic, arabic_font, arabic_font_size, max_width=scaled(900))

        # ===== ENGLISH TEXT =====
        draw.font = english_font
        draw.font_size = english_font_size
        draw.fill_color = Color("#FFFFFF")

        english_lines = wrap_text(draw, english, english_font, english_font_size, max_width=scaled(900))

        # ===== CALCULATE TOTAL HEIGHT FOR CENTERING =====
        arabic_line_spacing = scaled(25)
        english_line_spacing = scaled(10)

        total_height = (
            len(arabic_lines) * (arabic_font_size + arabic_line_spacing) +
            len(english_lines) * (english_font_size + english_line_spacing)
        )
        start_y = (poster_height - total_height) // 2

        # ===== DRAW ARABIC TEXT =====
        draw.font = arabic_font
        draw.font_style = 'italic'
        draw.font_size = arabic_font_size
        draw.font_weight = 700
        y_pos = start_y
        for line in arabic_lines:
            metrics = draw.get_font_metrics(img, line, True)
            text_width = metrics.text_width
            x_pos = (poster_width // 2) - (text_width // 2)
            draw.text(int(x_pos), int(y_pos), line)
            y_pos += arabic_font_size + arabic_line_spacing

        # ===== DRAW ENGLISH TRANSLATION =====
        draw.font = english_font
        draw.font_size = english_font_size
        for line in english_lines:
            metrics = draw.get_font_metrics(img, line, True)
            text_width = metrics.text_width
            x_pos = (poster_width // 2) - (text_width // 2)
            draw.text(int(x_pos), int(y_pos), line)
            y_pos += english_font_size + english_line_spacing

        # ===== ADDITIONAL INFO (EXACT FROM PRODUCTION) =====
        comp_date = f"Read 1 Ayah with us daily"
        draw.font = english_font
        draw.font_size = scaled(30)
        draw.fill_color = Color("#FFFFFF")
        
        comp_date_width = draw.get_font_metrics(img, comp_date, True).text_width
        comp_date_x_position = (poster_width // 2) - (comp_date_width // 2)
        draw.text(int(comp_date_x_position), scaled(1650), comp_date)

        comp_date = f"(Follow us and save this reel for daily Quran in your feed)"
        draw.font = english_font_small
        draw.font_size = scaled(30)
        draw.fill_color = Color("#FFFFFF")
        
        comp_date_width = draw.get_font_metrics(img, comp_date, True).text_width
        comp_date_x_position = (poster_width // 2) - (comp_date_width // 2)
        draw.text(int(comp_date_x_position), scaled(1680), comp_date)

        # ===== APPLY TEXT TO IMAGE =====
        draw(img)

        # ===== SAVE ORIGINAL IMAGE =====
        img.compression_quality = 100
        img.save(filename=output_path)
        img.close()

        # Supersample the poster for sharper text edges in the final 1080x1920 output.
        with PILImage.open(output_path) as poster_img:
            img_pil = poster_img.convert("RGB")
            img_pil = img_pil.resize((VIDEO_WIDTH, VIDEO_HEIGHT), PILImage.LANCZOS)
            img_pil.save(output_path, format="PNG", compress_level=0)

        log(f"✓ Poster created: {output_path}")
        return output_path

    except Exception as e:
        log(f"Error creating poster: {e}", "ERROR")
        return None


def fix_audio(input_video, output_video, force_silent=False):
    """EXACT from production"""
    try:
        if force_silent:
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-i", input_video,
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2",
                "-shortest",
                "-movflags", "+faststart",
                output_video
            ]
        else:
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-i", input_video,
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2",
                "-movflags", "+faststart",
                output_video
            ]

        result = subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        log(f"✓ Fixed audio: {output_video}")
        return output_video

    except subprocess.CalledProcessError as e:
        log(f"FFmpeg audio fix failed: {e.stderr}", "ERROR")
        return None


def create_quran_video(image_path, audio_path, video_output):
    """EXACT from production - uses ffmpeg_params"""
    if not mp:
        log("MoviePy not installed", "ERROR")
        return False

    try:
        log(f"Creating video from image + audio...")
        
        # Get audio duration
        audio = MP3(audio_path)
        duration = audio.info.length
        log(f"Audio duration: {duration:.2f}s")

        # Create video
        img_clip = mp.ImageClip(image_path)
        audio_clip = mp.AudioFileClip(audio_path)
        
        # EXACT from production
        img_clip = img_clip.set_duration(duration)
        img_clip = img_clip.set_position("center")
        img_clip = img_clip.set_fps(VIDEO_FPS)
        audio_clip = audio_clip.audio_fadeout(0.5)

        final_clip = img_clip.set_audio(audio_clip)

        # EXACT ffmpeg_params from production
        log(f"Encoding at {VIDEO_WIDTH}x{VIDEO_HEIGHT}, bitrate {VIDEO_BITRATE}, CRF={VIDEO_CRF}, preset={VIDEO_PRESET}...")
        final_clip.write_videofile(
            video_output,
            fps=VIDEO_FPS,
            codec="libx264",
            preset=VIDEO_PRESET,
            threads=4,
            bitrate=VIDEO_BITRATE,
            audio_codec="aac",
            audio_bitrate=AUDIO_BITRATE,
            ffmpeg_params=[
                "-crf", VIDEO_CRF,
                "-maxrate", VIDEO_MAXRATE,
                "-bufsize", VIDEO_BUFSIZE,
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-profile:v", "high",
                "-level", "4.2",
            ],
            verbose=False,
            logger=None,
        )

        audio_clip.close()
        final_clip.close()

        file_size_mb = os.path.getsize(video_output) / (1024 * 1024)
        log(f"✓ Video created: {video_output}")
        log(f"  Size: {file_size_mb:.2f} MB | Quality: {VIDEO_BITRATE} @ CRF={VIDEO_CRF}")
        return True

    except Exception as e:
        log(f"Error creating video: {e}", "ERROR")
        return False


def merge_videos(video_files, output_video, first_video_path, last_video_path):
    """EXACT from production - merges with first.mp4 and last.mp4"""
    try:
        list_file = Path(output_video).with_name("video_list_merge.txt")

        with open(list_file, "w") as f:
            # Fix and add first video (bismillah)
            first_fixed = first_video_path.replace(".mp4", "_fixed.mp4")
            fix_audio(first_video_path, first_fixed)
            f.write(f"file '{os.path.abspath(first_fixed)}'\n")

        # Add main videos
        with open(list_file, "a") as f:
            for video in video_files:
                abs_path = os.path.abspath(video)
                f.write(f"file '{abs_path}'\n")

        # Fix and add last video
        with open(list_file, "a") as f:
            last_fixed = last_video_path.replace(".mp4", "_fixed.mp4")
            fix_audio(last_video_path, last_fixed, force_silent=True)
            f.write(f"file '{os.path.abspath(last_fixed)}'\n")

        if video_files:
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
                "-r", str(VIDEO_FPS),
                "-crf", VIDEO_CRF,
                "-preset", VIDEO_PRESET,
                "-b:v", VIDEO_BITRATE,
                "-maxrate", VIDEO_MAXRATE,
                "-bufsize", VIDEO_BUFSIZE,
                "-profile:v", "high",
                "-level", "4.2",
                "-movflags", "+faststart",
                "-c:a", "aac", "-b:a", AUDIO_BITRATE,
                output_video
            ]

            log(f"Merging videos with bismillah + video + last...")
            result = subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            log(f"✓ Videos merged: {output_video}")
            
            # Cleanup temp files
            try:
                os.remove(list_file)
            except:
                pass

            return output_video

    except subprocess.CalledProcessError as e:
        log(f"FFmpeg merge failed: {e.stderr}", "ERROR")
        return None


def parse_ayah_range(ayah_spec):
    if "-" in ayah_spec:
        start, end = ayah_spec.split("-")
        return list(range(int(start), int(end) + 1))
    else:
        return [int(ayah_spec)]


def main():
    parser = argparse.ArgumentParser(description="Generate Quran sample videos (EXACT production quality)")
    parser.add_argument("--lang", choices=["en", "ur", "ar"], default="en", help="Language")
    parser.add_argument("--surah", type=int, required=True, help="Surah (1-114)")
    parser.add_argument("--ayah", type=str, required=True, help="Ayah or range (e.g., 14 or 14-20)")

    args = parser.parse_args()

    if args.surah < 1 or args.surah > 114:
        log("Surah must be 1-114", "ERROR")
        return

    ayahs = parse_ayah_range(args.ayah)
    lang_cfg = LANG_CONFIG.get(args.lang)
    
    if not lang_cfg:
        log(f"Language {args.lang} not supported", "ERROR")
        return

    # Setup output directory
    output_root = Path(__file__).parent.parent.parent / "sample_videos" / "quran" / args.lang
    output_root.mkdir(parents=True, exist_ok=True)

    log(f"Generating {len(ayahs)} video(s)")
    log(f"Surah: {args.surah}, Ayahs: {ayahs}")
    log(f"Output: {output_root}\n")

    # Get repo paths
    repo_path = Path(__file__).parent.parent.parent.parent / lang_cfg["repo_path"]
    fonts_dir = repo_path / "fonts"
    backgrounds_dir = repo_path / "background"

    if not fonts_dir.exists():
        log(f"Fonts directory not found: {fonts_dir}", "ERROR")
        return

    if not backgrounds_dir.exists():
        log(f"Background directory not found: {backgrounds_dir}", "ERROR")
        return

    # Use PNG backgrounds (1-26) for better quality instead of background_1.jpg
    png_backgrounds = [f for f in backgrounds_dir.glob("*.png") if f.name != "bkp"]
    if not png_backgrounds:
        log(f"No PNG backgrounds found in {backgrounds_dir}", "ERROR")
        return
    
    background_file = random.choice(png_backgrounds)
    log(f"Using background: {background_file.name}")

    # Check for first.mp4 and last.mp4
    first_video = backgrounds_dir / "bismiallah_short.mp4"
    last_video = backgrounds_dir / "last_short.mp4"

    if not first_video.exists():
        log(f"First video (bismiallah_short.mp4) not found: {first_video}", "ERROR")
        log("This file should exist in the background folder")
        return

    if not last_video.exists():
        log(f"Last video (last_short.mp4) not found: {last_video}", "ERROR")
        log("This file should exist in the background folder")
        return

    # Process each ayah
    for ayah in ayahs:
        log(f"\n{'='*70}")
        log(f"Processing: Surah {args.surah}, Ayah {ayah}")
        log(f"{'='*70}")

        # Fetch data
        data = fetch_quran_ayah(args.surah, ayah, args.lang)
        if not data:
            log("Failed to fetch data", "ERROR")
            continue

        log(f"Arabic: {data['arabic'][:60]}...")
        log(f"Translation: {data['english'][:60]}...")

        # File names
        base_name = f"surah_{args.surah:03d}_ayah_{ayah:03d}"
        audio_file = output_root / f"{base_name}.mp3"
        poster_file = output_root / f"{base_name}.png"
        video_file = output_root / f"{base_name}.mp4"
        final_video_file = output_root / f"{base_name}_final.mp4"

        # Download audio
        log("Downloading audio...")
        if not download_audio(data["audio_url"], str(audio_file)):
            log("Audio download failed", "ERROR")
            continue

        # Create poster
        log("Creating poster...")
        poster_path = create_poster(
            args.surah,
            ayah,
            data["arabic"],
            data["english"],
            data["surah_name"],
            data["surah_name_arabic"],
            data["en_name"],
            str(background_file),
            str(poster_file),
            fonts_dir=fonts_dir,
        )
        if not poster_path:
            log("Poster creation failed", "ERROR")
            continue

        # Create video
        log("Creating video...")
        if not create_quran_video(str(poster_path), str(audio_file), str(video_file)):
            log("Video creation failed", "ERROR")
            continue

        # Fix audio
        log("Fixing audio codec...")
        fixed_video_file = str(video_file).replace(".mp4", "_fixed.mp4")
        fixed_video = fix_audio(str(video_file), fixed_video_file)
        if not fixed_video:
            log("Audio fix failed", "ERROR")
            continue

        # Merge with first.mp4 and last.mp4
        log("Merging with bismillah intro and outro...")
        final_video = merge_videos([fixed_video], str(final_video_file), str(first_video), str(last_video))
        if not final_video:
            log("Video merge failed", "ERROR")
            continue

        log(f"\n✅ SUCCESS! Final video ready: {final_video}")
        log(f"   Format: Bismillah + Main Video + Outro")

    log(f"\n{'='*70}")
    log(f"All videos saved to: {output_root}")
    log(f"{'='*70}")


if __name__ == "__main__":
    main()
