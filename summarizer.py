import os
import requests
from openai import OpenAI

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
        }
    }
    
    response = requests.post(url, headers=headers, json=payload)
    return response.json().get('results', [])

def get_page_content(page_id):
    """Get the content from a Notion page, handling different media types"""
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    response = requests.get(url, headers=headers)
    blocks = response.json().get('results', [])
    
    content = []
    media_info = {
        'has_video': False,
        'has_image': False,
        'has_audio': False,
        'has_pdf': False,
        'has_embed': False,
        'video_count': 0,
        'image_count': 0,
        'audio_count': 0,
        'pdf_count': 0
    }
    
    for block in blocks:
        block_type = block.get('type')
        
        # Text content
        if block_type in ['paragraph', 'heading_1', 'heading_2', 'heading_3', 'bulleted_list_item', 'numbered_list_item', 'quote', 'callout']:
            text_array = block.get(block_type, {}).get('rich_text', [])
            for text_obj in text_array:
                content.append(text_obj.get('plain_text', ''))
        
        # Video (YouTube, Vimeo, etc.)
        elif block_type == 'video':
            media_info['has_video'] = True
            media_info['video_count'] += 1
            video_data = block.get('video', {})
            if video_data.get('type') == 'external':
                url = video_data.get('external', {}).get('url', '')
                content.append(f"[VIDEO: {url}]")
        
        # Image
        elif block_type == 'image':
            media_info['has_image'] = True
            media_info['image_count'] += 1
            image_data = block.get('image', {})
            caption = image_data.get('caption', [])
            if caption:
                caption_text = ' '.join([c.get('plain_text', '') for c in caption])
                content.append(f"[IMAGE: {caption_text}]")
            else:
                content.append("[IMAGE]")
        
        # Audio/Podcast
        elif block_type == 'audio':
            media_info['has_audio'] = True
            media_info['audio_count'] += 1
            content.append("[AUDIO/PODCAST]")
        
        # PDF
        elif block_type == 'pdf':
            media_info['has_pdf'] = True
            media_info['pdf_count'] += 1
            content.append("[PDF DOCUMENT]")
        
        # Embed (could be Spotify, SoundCloud, etc.)
        elif block_type == 'embed':
            media_info['has_embed'] = True
            embed_data = block.get('embed', {})
            url = embed_data.get('url', '')
            
            # Detect music/podcast platforms
            if any(platform in url.lower() for platform in ['spotify', 'soundcloud', 'apple.com/podcast']):
                media_info['has_audio'] = True
                content.append(f"[MUSIC/PODCAST: {url}]")
            # Detect video platforms
            elif any(platform in url.lower() for platform in ['youtube', 'vimeo', 'twitch']):
                media_info['has_video'] = True
                content.append(f"[VIDEO: {url}]")
            else:
                content.append(f"[EMBED: {url}]")
    
    text_content = ' '.join(content)
    return text_content, media_info

def generate_summary(content, media_info):
    """Generate punchy one-liner summary using OpenAI"""
    if not content or len(content.strip()) < 20:
        # Generate summary based on media type if no text
        if media_info['has_video']:
            return f"📹 {media_info['video_count']} video(s) • No text content"
        elif media_info['has_audio']:
            return "🎧 Audio/podcast content • No text content"
        elif media_info['has_pdf']:
            return f"📄 {media_info['pdf_count']} PDF(s) • No text content"
        elif media_info['has_image']:
            return f"🖼️ {media_info['image_count']} image(s) • No text content"
        else:
            return "Content too short to summarize"
    
    # Build media prefix
    media_prefix = ""
    if media_info['has_video']:
        media_prefix += "📹 "
    if media_info['has_audio']:
        media_prefix += "🎧 "
    if media_info['has_pdf']:
        media_prefix += "📄 "
    
    system_prompt = """You are an expert at creating ultra-concise summaries. 

Rules:
- Create a ONE-LINE summary (not a paragraph)
- Use short, punchy sentences separated by bullets (•)
- 3-5 key points maximum
- No full sentences needed - fragments are fine
- Be specific and informative
- Example style: "AI startup raises $100M • Focus on healthcare • CEO previously at Google • Launching Q2 2026"
- Example style: "New study on coffee • Reduces heart disease risk by 15% • 500k participants • Challenges previous research"
- Keep total under 200 characters if possible"""
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Summarize this in one punchy line with bullets:\n\n{content[:8000]}"}
        ],
        max_tokens=100,  # Reduced for shorter summaries
        temperature=0.3  # Lower temp for more focused output
    )
    
    summary = response.choices[0].message.content.strip()
    
    # Add media prefix if exists
    if media_prefix:
        summary = media_prefix + summary
    
    return summary

def update_page_summary(page_id, summary):
    """Update the TLDR property with the summary"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    
    payload = {
        "properties": {
            "TLDR": {
                "rich_text": [
                    {
                        "text": {
                            "content": summary[:2000]  # Notion has a 2000 char limit per rich_text
                        }
                    }
                ]
            }
        }
    }
    
    response = requests.patch(url, headers=headers, json=payload)
    return response.json()

def main():
    print("Starting summarization process...")
    
    # Get pages without summaries
    pages = get_pages_without_summary()
    print(f"Found {len(pages)} pages without summaries")
    
    for page in pages:
        page_id = page['id']
        page_title = page['properties'].get('Content', {}).get('title', [{}])[0].get('plain_text', 'Untitled')
        
        print(f"Processing: {page_title}")
        
        # Get page content and media info
        content, media_info = get_page_content(page_id)
        
        print(f"  → Found: {len(content)} chars text")
        if media_info['has_video']:
            print(f"  → Found: {media_info['video_count']} video(s)")
        if media_info['has_image']:
            print(f"  → Found: {media_info['image_count']} image(s)")
        if media_info['has_audio']:
            print(f"  → Found: audio/podcast")
        if media_info['has_pdf']:
            print(f"  → Found: {media_info['pdf_count']} PDF(s)")
        
        # Generate summary
        summary = generate_summary(content, media_info)
        print(f"  → Generated summary ({len(summary)} chars)")
        
        # Update the page
        update_page_summary(page_id, summary)
        print(f"  → Updated TLDR property")
    
    print("Done!")

if __name__ == "__main__":
    main()
