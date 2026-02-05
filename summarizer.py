import os
import requests
from openai import OpenAI
import concurrent.futures
import time

# Get environment variables
NOTION_TOKEN = os.environ['NOTION_TOKEN']
NOTION_DB_ID = os.environ['NOTION_DB_ID']
OPENAI_API_KEY = os.environ['OPENAI_API_KEY']

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Notion API headers
headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def get_pages_without_summary():
    """Query database for pages where TLDR is empty"""
    url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query"
    
    payload = {
        "filter": {
            "property": "TLDR",
            "rich_text": {
                "is_empty": True
            }
        },
        "page_size": 100  # Get up to 100 pages at once
    }
    
    response = requests.post(url, headers=headers, json=payload)
    return response.json().get('results', [])

def get_page_content(page_id):
    """Get the content from a Notion page, handling different media types"""
    url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"  # Limit to first 100 blocks
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        blocks = response.json().get('results', [])
    except Exception as e:
        print(f"    ⚠️  Error fetching blocks: {e}")
        return "", {
            'has_video': False,
            'has_image': False,
            'has_audio': False,
            'has_pdf': False,
            'video_count': 0,
            'image_count': 0,
            'audio_count': 0,
            'pdf_count': 0
        }
    
    content = []
    media_info = {
        'has_video': False,
        'has_image': False,
        'has_audio': False,
        'has_pdf': False,
        'video_count': 0,
        'image_count': 0,
        'audio_count': 0,
        'pdf_count': 0
    }
    
    # Limit content extraction to save time
    max_content_length = 10000
    current_length = 0
    
    for block in blocks:
        # Stop if we have enough content
        if current_length >= max_content_length:
            break
            
        block_type = block.get('type')
        
        # Text content
        if block_type in ['paragraph', 'heading_1', 'heading_2', 'heading_3', 'bulleted_list_item', 'numbered_list_item', 'quote', 'callout']:
            text_array = block.get(block_type, {}).get('rich_text', [])
            for text_obj in text_array:
                text = text_obj.get('plain_text', '')
                content.append(text)
                current_length += len(text)
        
        # Video
        elif block_type == 'video':
            media_info['has_video'] = True
            media_info['video_count'] += 1
        
        # Image
        elif block_type == 'image':
            media_info['has_image'] = True
            media_info['image_count'] += 1
        
        # Audio/Podcast
        elif block_type == 'audio':
            media_info['has_audio'] = True
            media_info['audio_count'] += 1
        
        # PDF
        elif block_type == 'pdf':
            media_info['has_pdf'] = True
            media_info['pdf_count'] += 1
        
        # Embed
        elif block_type == 'embed':
            embed_data = block.get('embed', {})
            url = embed_data.get('url', '').lower()
            
            if any(platform in url for platform in ['spotify', 'soundcloud', 'apple.com/podcast']):
                media_info['has_audio'] = True
            elif any(platform in url for platform in ['youtube', 'vimeo', 'twitch']):
                media_info['has_video'] = True
                media_info['video_count'] += 1
    
    text_content = ' '.join(content)[:10000]  # Hard limit
    return text_content, media_info

def detect_language(text):
    """Detect if text is primarily Korean or English"""
    if not text or len(text) < 10:
        return "en"
    
    # Sample first 500 chars for speed
    sample = text[:500]
    korean_chars = sum(1 for char in sample if '\uac00' <= char <= '\ud7a3')
    total_chars = sum(1 for c in sample if c.isalpha())
    
    if total_chars == 0:
        return "en"
    
    return "ko" if korean_chars / total_chars > 0.3 else "en"

def generate_summary(content, media_info):
    """Generate bullet-point summary using OpenAI"""
    
    # Quick returns for media-only content
    if media_info['has_video'] and len(content.strip()) < 100:
        return "📹"
    
    if len(content.strip()) < 20:
        if media_info['has_audio']:
            return "🎧"
        elif media_info['has_pdf']:
            return "📄"
        elif media_info['has_image']:
            return "🖼️"
        else:
            return "Content too short"
    
    # Detect language
    language = detect_language(content)
    
    # Build prompts
    if language == "ko":
        system_prompt = """간결한 요약 전문가입니다.

규칙:
- 한문장
- 짧고 구체적으로
- 100자 이내"""
        user_prompt = f"요약:\n\n{content[:6000]}"
    else:
        system_prompt = """Expert at concise summaries.

Rules:
- single sentence
- Short and specific
- Under 200 chars"""
        user_prompt = f"Summarize:\n\n{content[:6000]}"
    
    # Media prefix
    media_prefix = ""
    if media_info['has_video']:
        media_prefix = "📹 "
    elif media_info['has_audio']:
        media_prefix = "🎧 "
    elif media_info['has_pdf']:
        media_prefix = "📄 "
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=120,
            temperature=0.3,
            timeout=15  # 15 second timeout
        )
        
        summary = response.choices[0].message.content.strip()
        return media_prefix + summary if media_prefix else summary
        
    except Exception as e:
        print(f"    ⚠️  OpenAI error: {e}")
        return "Error generating summary"

def update_page_summary(page_id, summary):
    """Update the TLDR property with the summary"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    
    payload = {
        "properties": {
            "TLDR": {
                "rich_text": [
                    {
                        "text": {
                            "content": summary[:2000]
                        }
                    }
                ]
            }
        }
    }
    
    try:
        response = requests.patch(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"    ⚠️  Error updating page: {e}")
        return None

def process_single_page(page):
    """Process a single page (for parallel execution)"""
    page_id = page['id']
    page_title = page['properties'].get('Content', {}).get('title', [{}])[0].get('plain_text', 'Untitled')
    
    print(f"Processing: {page_title[:60]}...")
    
    start_time = time.time()
    
    # Get content
    content, media_info = get_page_content(page_id)
    
    # Quick stats
    stats = []
    if len(content) > 0:
        stats.append(f"{len(content)} chars")
    if media_info['has_video']:
        stats.append(f"{media_info['video_count']} video(s)")
    if media_info['has_image']:
        stats.append(f"{media_info['image_count']} image(s)")
    
    print(f"  → {', '.join(stats) if stats else 'No content'}")
    
    # Language detection
    lang = detect_language(content)
    print(f"  → Language: {lang}")
    
    # Generate summary
    summary = generate_summary(content, media_info)
    print(f"  → Summary: {summary[:80]}...")
    
    # Update page
    update_page_summary(page_id, summary)
    
    elapsed = time.time() - start_time
    print(f"  → Completed in {elapsed:.1f}s")
    
    return page_title, summary

def main():
    print("Starting summarization process...")
    overall_start = time.time()
    
    # Get pages
    pages = get_pages_without_summary()
    print(f"Found {len(pages)} pages without summaries\n")
    
    if not pages:
        print("Nothing to do!")
        return
    
    # Process pages in parallel (max 3 at a time to avoid rate limits)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(process_single_page, pages))
    
    elapsed_total = time.time() - overall_start
    print(f"\n✅ Done! Processed {len(pages)} pages in {elapsed_total:.1f}s")
    print(f"   Average: {elapsed_total/len(pages):.1f}s per page")

if __name__ == "__main__":
    main()
