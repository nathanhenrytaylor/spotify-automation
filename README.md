# SPOTIFY AUTOMASHUN — LISTEN UP YA GROTZ

Right, ya runty grotz! Dis repoz fer bossin' Spotify 'round wiv Python skripts so any git can get der muziks krumped da way dey want. We use refresh tokenz, playlists, an' logs dat spin fer a year. Do it propper or get a boot in da teef.

## Wot'z Inside
- `Junk_Mover/junk_mover.py`: Da big mek-job. Grabs trackz from yer source list an' chucks old ones into year Junk Drawer lists. Logs everyfing.
- `Junk_Mover/generate_refresh_token.py`: Manual token lootin'. Opens browser, you paste back da callback URL, it nicks da code an' shoves da fresh token into `.env`.
- `Junk_Mover/spotify_profile.py`: Quick sniff ta make sure yer creds werk by yankin' yer profile.

## Wot Gear Ya Need (ENV)
Stuff dese in `.env` or get krumped:
```
JUNK_MOVER_CLIENT_ID=                                   # from yer Spotify app
JUNK_MOVER_CLIENT_SECRET=                               # same app secret
JUNK_MOVER_REFRESH_TOKEN=                               # fetched wiv da generator
JUNK_MOVER_REDIRECT_URI=https://example.com/callback    # any registered HTTPS URL; only needed for first-time manual token grab
JUNK_MOVER_SOURCE_PLAYLIST=The Big Junk Drawer          # or wotever ya call it
JUNK_MOVER_DURATION_DAYS=180                            # how old before it getz kicked (0 means today or older)
```

## How ta Loot a Refresh Token
1. Make sure yer Spotify app haz redirect URI set to wot ya put in `JUNK_MOVER_REDIRECT_URI` (e.g., `https://example.com/callback`) — exact or else da humie API screams.
2. `python Junk_Mover/generate_refresh_token.py`
3. Browser pops. Approve da scopes (`user-read-email user-read-private playlist-modify-public playlist-modify-private`).
4. Copy da whole redirected URL, paste it back. Script rips out da code, trades it for tokenz, and stuffs `JUNK_MOVER_REFRESH_TOKEN` into `.env`.

## How ta Krump Playlists
1. Fill `.env` like above.
2. `python Junk_Mover/junk_mover.py`
3. It logs in as whichever Spotify humie owns da refresh token, finds `JUNK_MOVER_SOURCE_PLAYLIST`, sniffs tracks older than `JUNK_MOVER_DURATION_DAYS`, then punts 'em into `YY Junk Drawer` (makin' da list if it ain't dere), an' drops logs in `logs/junk_mover.log` (rotates daily, keeps a year).

## Wot's Dis “Big Junk Drawer”?
- Set `JUNK_MOVER_SOURCE_PLAYLIST=The Big Junk Drawer` (or yer own name) as da dump bin fer every song ya add.
- Da script sorts 'em by year-added into `YY Junk Drawer` lists. Right now it's based on when it hit da Big Junk Drawer, but you could get kunnin' later and sort by track traits (tempo, energy, mood) if ya fancy hackin' da code.

## Testin' Da “Zero Dayz” Bit
- Set `JUNK_MOVER_DURATION_DAYS=0` and run `python Junk_Mover/junk_mover.py`. Anyfin' added today or earlier getz moved. If ya set negative, da script yellz at ya.

## Troubleshootin'
- `403 Insufficient client scope`: Ya forgot to regen token after we added playlist scopes. Run da generator again and approve da scopes.
- `INVALID_CLIENT / Insecure redirect`: Yer redirect in the app don't match `.env`. Fix it and try again.
- Browser don't open? Just paste da authorize URL printed by da generator.

Now quit readin' an' get back ta fightin' wiv yer playlists. Red wunz go fasta, but logs go fasta too, so check `logs/junk_mover.log` if yer mucked it up.

## Kreditz
- Orky gabbin' inspired by **[Ork Speech: 101 By Gashmangla Rexum](https://steamcommunity.com/sharedfiles/filedetails/?id=1122960642)**.
