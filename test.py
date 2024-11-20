from youtube_transcript_api import YouTubeTranscriptApi

def get_combined_text(video_id):
    transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['ko'])
    combined_text = ' '.join([content['text'] for content in transcript])
    return combined_text

video_id = "3XbtEX3jUv4"
combined_text = get_combined_text(video_id)
print(combined_text)