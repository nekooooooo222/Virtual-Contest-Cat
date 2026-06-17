import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import datetime
import requests
import random
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ==========================================
# 本番用：環境変数から設定を読み込む
# ==========================================
# ※ここは絶対に実際のトークンを直接書かないでください！（GitHubで公開されてしまいます）
# Renderの管理画面で後から入力します。
TOKEN = os.getenv("DISCORD_TOKEN") 
DATA_CHANNEL_ID = int(os.getenv("DATA_CHANNEL_ID", 0))

# データ保持用変数
users_data = {}
history_data = []
data_message_id = None # データを書き込んでいるメッセージのID

scheduler = AsyncIOScheduler()
vcon_sessions = {}

# ==========================================
# データベースの代わり（Discordに保存する機能）
# ==========================================
async def load_data_from_channel(bot):
    global users_data, history_data, data_message_id
    if DATA_CHANNEL_ID == 0: return

    channel = bot.get_channel(DATA_CHANNEL_ID)
    if not channel:
        print("エラー: データ保存用チャンネルが見つかりません。")
        return

    async for msg in channel.history(limit=10):
        if msg.author == bot.user and msg.content.startswith("```json"):
            try:
                json_str = msg.content.strip("`").removeprefix("json\n")
                data = json.loads(json_str)
                users_data = data.get("users", {})
                history_data = data.get("history", [])
                data_message_id = msg.id
                print("Discordチャンネルからデータを復元したにゃ！")
                return
            except Exception as e:
                print("データのパースエラー:", e)

    print("データが見つからないため、新規作成するにゃ。")
    await save_data_to_channel(bot)

async def save_data_to_channel(bot):
    global data_message_id
    if DATA_CHANNEL_ID == 0: return
    
    channel = bot.get_channel(DATA_CHANNEL_ID)
    if not channel: return

    data = {
        "users": users_data,
        "history": history_data
    }
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    content = f"```json\n{json_str}\n```"

    if data_message_id:
        try:
            msg = await channel.fetch_message(data_message_id)
            await msg.edit(content=content)
            return
        except discord.NotFound:
            pass

    msg = await channel.send(content)
    data_message_id = msg.id

# ==========================================
# UIコンポーネント（ボタン機能）
# ==========================================
class VconJoinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="参加する / キャンセル", style=discord.ButtonStyle.green, custom_id="join_vcon_btn")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        
        if user_id not in users_data:
            await interaction.response.send_message("AtCoder IDが未登録だにゃ！先に `/register` で登録するにゃ～", ephemeral=True)
            return

        msg_id = interaction.message.id
        if msg_id not in vcon_sessions:
            vcon_sessions[msg_id] = set()

        if user_id in vcon_sessions[msg_id]:
            vcon_sessions[msg_id].remove(user_id)
            await interaction.response.send_message("参加をキャンセルしたにゃ", ephemeral=True)
        else:
            vcon_sessions[msg_id].add(user_id)
            await interaction.response.send_message("参加登録したにゃ！開始まで待っておくにゃ～", ephemeral=True)

        participants_mentions = [f"<@{uid}>" for uid in vcon_sessions[msg_id]]
        join_text = " ".join(participants_mentions) if participants_mentions else "まだいないにゃ"
        
        base_content = interaction.message.content.split("\n\n**【現在の参加者】**")[0]
        new_content = f"{base_content}\n\n**【現在の参加者】**\n{join_text}"
        
        await interaction.message.edit(content=new_content)

# ==========================================
# Botの設定とコマンド
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

async def setup_hook():
    bot.add_view(VconJoinView())
bot.setup_hook = setup_hook

@bot.event
async def on_ready():
    print(f'ログインしました: {bot.user.name}')
    
    # 起動時にDiscordチャンネルからデータを読み込む
    await load_data_from_channel(bot)
    
    if not scheduler.running:
        scheduler.start()
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)}個のコマンドを同期しました。")
    except Exception as e:
        print(e)

@bot.tree.command(name="register", description="自分のAtCoder IDをBotに登録するにゃ")
@app_commands.describe(atcoder_id="あなたのAtCoder IDを入力してにゃ")
async def register(interaction: discord.Interaction, atcoder_id: str):
    user_id = str(interaction.user.id)
    users_data[user_id] = atcoder_id
    
    # 登録されたらDiscordチャンネルに保存
    await save_data_to_channel(bot)
    
    await interaction.response.send_message(
        f"{interaction.user.mention} さんのAtCoder IDを `{atcoder_id}` として登録したにゃ～", 
        ephemeral=False
    )

@bot.tree.command(name="vcontest", description="バチャコンの募集を開始するにゃ")
@app_commands.describe(start_time="開始日時 (例: 2026-06-18 21:00)")
async def vcontest(interaction: discord.Interaction, start_time: str):
    try:
        dt = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M")
    except ValueError:
        await interaction.response.send_message("日時のフォーマットが違うにゃ！`2026-06-18 21:00` のように入力してにゃ～", ephemeral=True)
        return
    
    # 本番用：90分前に処理を実行
    run_time = dt - datetime.timedelta(minutes=90) 
    now = datetime.datetime.now()

    if run_time < now:
        await interaction.response.send_message("開始時間が近すぎるにゃ。もっと後の時間を指定するにゃ～", ephemeral=True)
        return

    base_text = (
        f"📢 **バチャコン募集！**\n"
        f"開始時間: {dt.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"参加する人は下のボタンを押すんだにゃ！\n"
        f"(*{run_time.strftime('%H:%M')} に、ねこが参加者の未プレイ問題から回を自動決定するにゃ*)"
    )

    await interaction.response.send_message(
        f"{base_text}\n\n**【現在の参加者】**\nまだいないにゃ",
        view=VconJoinView()
    )
    
    msg = await interaction.original_response()
    vcon_sessions[msg.id] = set()

    scheduler.add_job(decide_vcontest, 'date', run_date=run_time, args=[interaction.channel_id, msg.id])
    print(f"募集開始: {start_time}")

# ==========================================
# 評価関数・コアロジック
# ==========================================
async def decide_vcontest(channel_id, message_id):
    channel = bot.get_channel(channel_id)
    if not channel: return
    
    participants_discord_ids = list(vcon_sessions.get(message_id, set()))
    if not participants_discord_ids:
        await channel.send("参加者がいなかったので、今回のバチャコンは中止にゃ！")
        return

    atcoder_ids = [users_data[d_id] for d_id in participants_discord_ids]
    status_msg = await channel.send(f"**開始するコンテストの決定処理を開始するにゃ！**\n(参加者: {', '.join(atcoder_ids)})\n`AtCoder Problemsからコンテスト情報を取得中にゃ...`")

    try:
        contests_res = await asyncio.to_thread(requests.get, "https://kenkoooo.com/atcoder/resources/contests.json")
        contests_data = contests_res.json()
    except Exception as e:
        await channel.send("APIからのコンテスト情報の取得に失敗したにゃ...")
        return
        
    target_contests = set()
    for c in contests_data:
        cid = c["id"]
        if cid.startswith("abc"):
            try:
                num = int(cid[3:6])
                if num >= 126 and cid not in history_data:
                    target_contests.add(cid)
            except:
                pass

    if not target_contests:
        await channel.send("対象となるコンテストがもうないにゃ！")
        return

    user_ac_data = {} 
    
    for i, user in enumerate(atcoder_ids):
        await status_msg.edit(content=f"**開始するコンテストの決定処理を開始するにゃ！**\n(参加者: {', '.join(atcoder_ids)})\n`データ取得中... ({i+1}/{len(atcoder_ids)}人完了にゃ)`")
        user_ac_data[user] = {}
        try:
            url = f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={user}"
            await asyncio.sleep(1.0) 
            sub_res = await asyncio.to_thread(requests.get, url)
            if sub_res.status_code == 200:
                subs = sub_res.json()
                for sub in subs:
                    if sub["result"] == "AC":
                        cid = sub["contest_id"]
                        if cid in target_contests:
                            idx = sub["problem_id"].split("_")[-1] 
                            if cid not in user_ac_data[user]:
                                user_ac_data[user][cid] = set()
                            user_ac_data[user][cid].add(idx)
        except Exception as e:
            print(f"Failed to fetch for {user}: {e}")

    await status_msg.edit(content=f" `全員のデータを取得完了！計算中にゃ～`")

    def is_f_or_later(idx):
        if len(idx) > 1: return True 
        return idx >= 'f'

    valid_contests = [] 
    
    for cid in target_contests:
        num = int(cid[3:6])
        is_6_problems = (126 <= num <= 211)
        
        exclude = False
        total_score = 0
        score_4_over_count = 0
        
        for user in atcoder_ids:
            ac_set = user_ac_data[user].get(cid, set())
            
            if len(ac_set) >= 5 or any(is_f_or_later(idx) for idx in ac_set):
                exclude = True
                break
                
            score = 0
            if is_6_problems:
                if 'd' in ac_set: score += 1.5
                if 'e' in ac_set: score += 4
            else:
                if 'd' in ac_set: score += 1
                if 'e' in ac_set: score += 3
                
            total_score += score
            if score >= 4:
                score_4_over_count += 1
                
        if exclude:
            continue
            
        if (score_4_over_count / len(atcoder_ids)) >= 0.35:
            continue
            
        valid_contests.append((cid, total_score))

    if not valid_contests:
        await channel.send("対象となるコンテストがもうないにゃ！")
        return

    valid_contests.sort(key=lambda x: x[1])
    top_30 = valid_contests[:30]

    scores = [(cid, score + 1) for cid, score in top_30]
    min_score_6 = scores[0][1] ** 6
    
    weights = []
    for cid, s in scores:
        weight = min_score_6 / (s ** 6)
        weights.append(weight)
        
    chosen_cid = random.choices([c for c, s in scores], weights=weights, k=1)[0]
    
    # 履歴を更新してDiscordチャンネルに保存
    history_data.append(chosen_cid)
    await save_data_to_channel(bot)
    
    await status_msg.delete()
    await channel.send(
        f"**今回のバチャコンの回が決定しました！！**\n"
        f"👉 **{chosen_cid.upper()}** (https://atcoder.jp/contests/{chosen_cid})\n\n"
        f"準備して待機するにゃ～！"
    )

# ==========================================
# Renderの無料枠を騙すためのダミーWebサーバー
# ==========================================
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_server():
    # Renderが指定するポート番号を読み込んでサーバーを立てる
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

# Botの処理とは別の裏口（スレッド）でダミーサーバーを動かす
threading.Thread(target=run_server, daemon=True).start()

# --- この下に元々ある bot.run(TOKEN) が来ます ---
bot.run(TOKEN)
