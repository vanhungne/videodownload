# How to Import Cookies for YouTube Downloads

## Why Import Cookies?

YouTube sometimes requires authentication to download certain videos, especially when you encounter errors like:
- "n challenge solving failed"
- "Only images are available for download"
- "Requested format is not available"
- Age-restricted or members-only content

Importing cookies from your browser allows yt-dlp to use your YouTube login session.

## Method 1: Export Cookies Using Browser Extension (Recommended)

### For Chrome/Edge:
1. Install the **"Get cookies.txt LOCALLY"** extension:
   - Chrome: https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc
   - Edge: Search for "Get cookies.txt LOCALLY" in Edge Add-ons

2. Go to YouTube (https://www.youtube.com) and make sure you're logged in

3. Click the extension icon → Click "Export" → Save as `cookies.txt`

4. In the app, click **"🍪 Import Cookie"** button

5. Select your `cookies.txt` file

### For Firefox:
1. Install the **"cookies.txt"** extension:
   - Firefox: https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/

2. Go to YouTube (https://www.youtube.com) and make sure you're logged in

3. Click the extension icon → Export cookies

4. In the app, click **"🍪 Import Cookie"** button

5. Select your `cookies.txt` file

## Method 2: Manual Export (Advanced)

If you prefer not to use extensions, you can manually export cookies:

1. Open Developer Tools (F12) in your browser
2. Go to YouTube and login
3. In Developer Tools, go to **Application** tab (Chrome) or **Storage** tab (Firefox)
4. Find **Cookies** → `https://www.youtube.com`
5. You'll need to manually format these into Netscape cookie format

**Note:** This method is complex and not recommended for most users.

## After Importing

Once you've imported cookies:
- The app will automatically use them for YouTube downloads
- Cookie file location: `D:\MyTool\YoutubeTranscript\cookies.txt`
- Cookies are used for all YouTube downloads automatically
- You may need to re-import cookies if they expire (usually every few months)

## Retry Failed Downloads

After importing cookies, use the **"🔄 Retry Fail"** button to retry all failed downloads with your new authentication.

## Security Notes

⚠️ **Important:**
- Your cookies contain sensitive authentication data
- Don't share your `cookies.txt` file with anyone
- The cookies are stored locally in your app directory
- Consider deleting the file when you're done if using a shared computer

## Troubleshooting

### Still Getting Errors After Importing?
1. Make sure you're logged into YouTube in your browser
2. Export fresh cookies (cookies may expire)
3. Try using a different browser
4. Update yt-dlp: `pip install -U yt-dlp`

### Format Not Available?
- Some videos require specific quality settings
- Try lowering the quality (720p or 480p)
- Check if the video is region-restricted

### Members-Only Content?
- Even with cookies, you can only download content you have access to
- For members-only videos, you need an active membership on that channel

## Additional Help

If you continue to experience issues:
1. Check the **Logs** tab for detailed error messages
2. Try the "🔄 Retry Fail" button after importing cookies
3. Consider updating yt-dlp to the latest nightly version
