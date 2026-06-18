# ==========================================
# 【デバッグ用】スクレイピング挙動テストコマンド
# ==========================================
@bot.tree.command(name="test_scrape", description="RenderからのスクレイピングとAPIの挙動をテストするにゃ")
@app_commands.describe(contest_id="コンテストID (例: abc210)", user_id="AtCoder ID (例: nekooooooo)")
async def test_scrape(interaction: discord.Interaction, contest_id: str, user_id: str):
    await interaction.response.defer() # 処理に時間がかかるので待機状態にする
    
    log_text = f"🔍 **テスト開始 ({contest_id} / {user_id})**\n\n"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    # 1. results/json (パフォ計算データ) の直アクセス・テスト
    try:
        r_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/contests/{contest_id}/results/json", headers=headers, timeout=10)
        log_text += f"**[1. results/json 直アクセス]**\nStatus: {r_res.status_code}\nContent(先頭150文字):\n```html\n{r_res.text[:150]}\n```\n"
    except Exception as e:
        log_text += f"**[1. results/json 直アクセス]**\nError: {e}\n\n"

    # 2. ログイン処理のテスト
    try:
        session = requests.Session()
        session.headers.update(headers)
        login_url = "https://atcoder.jp/login"
        res = session.get(login_url, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        csrf_token_tag = soup.find(attrs={"name": "csrf_token"})
        
        if csrf_token_tag:
            csrf_token = csrf_token_tag.get("value")
            login_post = session.post(login_url, data={
                "username": ATCODER_USERNAME,
                "password": ATCODER_PASSWORD,
                "csrf_token": csrf_token
            }, timeout=10)
            log_text += f"**[2. ログイン処理]**\nStatus: {login_post.status_code}\n終了後のURL: {login_post.url}\n"
            
            # ログイン状態で results/json を取ってみる
            login_r_res = session.get(f"https://atcoder.jp/contests/{contest_id}/results/json", timeout=10)
            log_text += f"取得データ(先頭150文字):\n```html\n{login_r_res.text[:150]}\n```\n"
        else:
            log_text += "**[2. ログイン処理]**\nCSRFトークンが見つからなかったにゃ(Cloudflareに弾かれた可能性大)。\n\n"
    except Exception as e:
        log_text += f"**[2. ログイン処理]**\nError: {e}\n\n"

    # 3. 提出ページのスクレイピングテスト
    try:
        url = f"https://atcoder.jp/contests/{contest_id}/submissions?f.User={user_id}"
        sub_res = await asyncio.to_thread(requests.get, url, headers=headers)
        soup = BeautifulSoup(sub_res.text, 'html.parser')
        rows = soup.select('table tbody tr')
        
        log_text += f"**[3. 提出ページ取得]**\nStatus: {sub_res.status_code}\n取得した提出行数: {len(rows)}\n"
        if rows:
            cells = rows[0].find_all('td')
            if len(cells) >= 8:
                log_text += f"最新の提出: {cells[0].text.strip()} | 問題: {cells[1].text.strip()} | 結果: {cells[6].text.strip()}\n"
    except Exception as e:
        log_text += f"**[3. 提出ページ取得]**\nError: {e}\n"

    await interaction.followup.send(log_text)
