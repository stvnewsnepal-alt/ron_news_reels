import os, requests
from config import PAGE_ID,PAGE_TOKEN,GRAPH_API_VERSION

BASE=f"https://graph.facebook.com/{GRAPH_API_VERSION}"

def publish_reel(video_path,story):
    """
    Upload as a Facebook Page video (4:5, 1080x1350).
    The Page Videos endpoint is used intentionally: do not send 4:5 media
    to a Reels-only endpoint that may enforce vertical 9:16 requirements.
    """
    if not PAGE_ID or not PAGE_TOKEN:
        raise RuntimeError("Set FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN in GitHub Secrets.")
    caption=f'{story["title"]}\n\nपूरा समाचार: {story["url"]}\n\n#HamroChitwan #News'
    with open(video_path,"rb") as f:
        response=requests.post(
            f"{BASE}/{PAGE_ID}/videos",
            data={
                "title":story["title"][:200],
                "description":caption,
                "access_token":PAGE_TOKEN,
                "published":"true",
            },
            files={"source":(os.path.basename(video_path),f,"video/mp4")},
            timeout=900
        )
    response.raise_for_status()
    result=response.json()
    if not result.get("id"):
        raise RuntimeError(f"Facebook did not return a video ID: {result}")
    return result
