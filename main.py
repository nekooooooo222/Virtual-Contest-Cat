import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import datetime
from datetime import timezone, timedelta
import requests
import random
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from bs4 import BeautifulSoup

# ==========================================
# 設定とデータベース
# ==========================================
TOKEN = os.getenv("DISCORD_TOKEN") 
DATA_CHANNEL_ID = int(os.getenv("DATA_CHANNEL_ID", 0))
REVEL_SESSION = os.getenv("REVEL_SESSION")

users_data = {}
history_data = []
data_message_id = None 

scheduler = AsyncIOScheduler()
vcon_sessions = {}
JST = timezone(timedelta(hours=9))

async def load_data_from_channel(bot):
    global users_data, history_data, data_message_id
    if DATA_CHANNEL_ID == 0: return
    channel = bot.get_channel(DATA_CHANNEL_ID)
    if not channel: return

    async for msg in channel.history(limit=50):
        if msg.author == bot.user and "```json" in msg.content:
            try:
                json_str = msg.content.split("```json")[1].split("```")[0].strip()
                data = json.loads(json_str)
                users_data = data.get("users", {})
                history_data = data.get("history", [])
                data_message_id = msg.id
                print("Discordチャンネルからデータを復元したにゃ！")
                return
            except Exception as e: 
                print(f"データパースエラー: {e}")

    await save_data_to_channel(bot)

async def save_data_to_channel(bot):
    global data_message_id
    if DATA_CHANNEL_ID == 0: return
    channel = bot.get_channel(DATA_CHANNEL_ID)
    if not channel: return

    data = {"users": users_data, "history": history_data}
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    content = f"```json\n{json_str}\n```"

    if data_message_id:
        try:
            msg = await channel.fetch_message(data_message_id)
            await msg.edit(content=content)
            return
        except discord.NotFound: pass

    msg = await channel.send(content)
    data_message_id = msg.id

# ==========================================
# UIコンポーネント
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
        if msg_id not in vcon_sessions: vcon_sessions[msg_id] = set()

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
    await load_data_from_channel(bot)
    if not scheduler.running: scheduler.start()
    await bot.tree.sync()

@bot.tree.command(name="register", description="自分のAtCoder IDをBotに登録するにゃ")
@app_commands.describe(atcoder_id="あなたのAtCoder IDを入力してにゃ")
async def register(interaction: discord.Interaction, atcoder_id: str):
    users_data[str(interaction.user.id)] = atcoder_id
    await save_data_to_channel(bot)
    await interaction.response.send_message(f"{interaction.user.mention} さんのAtCoder IDを `{atcoder_id}` として登録したにゃ～", ephemeral=False)

@bot.tree.command(name="test_scrape", description="RenderからのAPI挙動をテストするにゃ")
@app_commands.describe(contest_id="コンテストID", user_id="AtCoder ID")
async def test_scrape(interaction: discord.Interaction, contest_id: str, user_id: str):
    await interaction.response.defer() 
    log_text = f"🔍 **Cookie突破テスト ({contest_id} / {user_id})**\n\n"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    cookies = {'REVEL_SESSION': REVEL_SESSION} if REVEL_SESSION else {}

    try:
        s_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/contests/{contest_id}/standings/json", headers=headers, cookies=cookies, timeout=10)
        if s_res.status_code == 200:
            log_text += f"**[1. 本家 standings/json]**\nStatus: 200\n✅ **大成功！Cookie突破したにゃ！**\n\n"
        else:
            log_text += f"**[1. 本家 standings/json]**\nStatus: {s_res.status_code}\n\n"
    except Exception as e: log_text += f"Error: {e}\n\n"

    try:
        url = f"https://atcoder.jp/contests/{contest_id}/submissions?f.User={user_id}"
        sub_res = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, timeout=10)
        soup = BeautifulSoup(sub_res.text, 'html.parser')
        rows = soup.select('table tbody tr')
        log_text += f"**[2. 本家 提出スクレイピング]**\nStatus: {sub_res.status_code}\n"
        if rows: log_text += f"✅ **大成功！提出一覧を取得できたにゃ！**\n"
        else: log_text += "取得した提出行数: 0\n"
    except Exception as e: log_text += f"Error: {e}\n"
    await interaction.followup.send(log_text)

@bot.tree.command(name="vcontest", description="バチャコンの募集を開始するにゃ")
@app_commands.describe(start_time="開始日時 (例: 2026-06-18 21:00)")
async def vcontest(interaction: discord.Interaction, start_time: str):
    try:
        dt = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    except ValueError:
        await interaction.response.send_message("日時のフォーマットが違うにゃ！", ephemeral=True)
        return
    
    now = datetime.datetime.now(JST)
    # 【テスト用】開始の「1分前」に決定処理を実行
    run_time = dt - datetime.timedelta(minutes=1) 
    
    if run_time < now:
        if dt < now:
            return await interaction.response.send_message("開始時間が過去だにゃ。", ephemeral=True)
        run_time = now + datetime.timedelta(seconds=30) # 間に合わなければ30秒後に強制決定

    base_text = (
        f"📢 **【最終テスト】バチャコン募集！**\n"
        f"開始時間: {dt.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"参加する人は下のボタンを押すんだにゃ！\n"
        f"(*{run_time.strftime('%H:%M')} に、回を自動決定するにゃ*)"
    )

    await interaction.response.send_message(f"{base_text}\n\n**【現在の参加者】**\nまだいないにゃ", view=VconJoinView())
    msg = await interaction.original_response()
    vcon_sessions[msg.id] = set()

    scheduler.add_job(decide_vcontest, 'date', run_date=run_time, args=[interaction.channel_id, msg.id, dt])

# ==========================================
# 評価関数・コンテスト決定
# ==========================================
async def decide_vcontest(channel_id, message_id, start_dt):
    channel = bot.get_channel(channel_id)
    if not channel: return
    
    participants_discord_ids = list(vcon_sessions.get(message_id, set()))
    if not participants_discord_ids: return await channel.send("中止にゃ！")

    atcoder_ids = [users_data[d_id] for d_id in participants_discord_ids]
    status_msg = await channel.send(f"**決定処理を開始するにゃ！**\n`データ取得中...`")

    # 【テスト用】評価関数を完全にスキップしてABC158に強制固定！！
    chosen_cid = "abc158"
    await asyncio.sleep(2.0) # ちょっとだけ演出のタメ
    
    history_data.append(chosen_cid)
    await save_data_to_channel(bot)
    
    await status_msg.delete()
    await channel.send(
        f"**今回のバチャコンの回が決定しました！！**\n👉 **{chosen_cid.upper()}** (https://atcoder.jp/contests/{chosen_cid})\n"
        f"開始時間は **{start_dt.strftime('%H:%M')}** だにゃ！\n*(※超爆速テスト用: 1分間バチャ。提出制限解除！)*"
    )

    # 【テスト用】終了時刻(1分後) + 1分後 に結果発表を予約！(合計2分後)
    end_time = start_dt + datetime.timedelta(minutes=2)
    scheduler.add_job(
        aggregate_vcontest, 'date', run_date=end_time, 
        args=[channel_id, chosen_cid, participants_discord_ids, start_dt]
    )

# ==========================================
# ハイブリッド方式：自動集計・パフォ計算
# ==========================================
async def aggregate_vcontest(channel_id, cid, discord_ids, start_dt):
    channel = bot.get_channel(channel_id)
    if not channel: return
    await channel.send(f"🏁 **{cid.upper()} バチャコン終了！！**\n`ただいま結果とパフォーマンスを集計中にゃ...`")

    start_epoch = int(start_dt.timestamp())
    # 【テスト用】コンテスト時間は1分間（1 * 60秒）
    end_epoch = start_epoch + 1 * 60

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    cookies = {'REVEL_SESSION': REVEL_SESSION} if REVEL_SESSION else {}

    try:
        s_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/contests/{cid}/standings/json", headers=headers, cookies=cookies, timeout=20)
        r_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/contests/{cid}/results/json", headers=headers, cookies=cookies, timeout=20)
        standings = s_res.json()
        results = r_res.json()
    except Exception as e:
        return await channel.send(f"本番データの取得に失敗してパフォが計算できないにゃ... (`{e}`)")

    tasks = [t["Assignment"] for t in standings["TaskInfo"]] 
    
    ranking_data = []
    for d_id in discord_ids:
        user = users_data.get(d_id)
        if not user: continue
        
        subs = []
        for page in range(1, 3):
            url = f"https://atcoder.jp/contests/{cid}/submissions?page={page}&f.User={user}"
            await asyncio.sleep(1.0)
            res = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies)
            soup = BeautifulSoup(res.text, 'html.parser')
            rows = soup.select('table tbody tr')
            if not rows: break

            for row in rows:
                cells = row.find_all('td')
                if len(cells) < 8: continue
                time_tag = cells[0].find('time')
                if not time_tag: continue

                try:
                    sub_dt = datetime.datetime.strptime(time_tag.text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
                    sub_epoch = int(sub_dt.timestamp())
                except: continue

                # 【テスト用】時間制限のフィルタリングを削除！全部拾うにゃ！
                task_link = cells[1].find('a')
                task_idx = task_link.get('href', '').split('_')[-1].upper() if task_link else 'A'
                score_text = cells[4].text.strip()
                score = float(score_text) if score_text.replace('.', '', 1).isdigit() else 0
                result_label = cells[6].find('span')
                result = result_label.text.strip() if result_label else "WJ"

                subs.append({"epoch_second": sub_epoch, "problem_id": task_idx, "result": result, "point": score})
            if len(rows) < 20: break 

        subs.sort(key=lambda x: x["epoch_second"])
        
        problem_status = {}
        total_score = 0
        last_ac_time = 0
        total_penalties = 0

        for sub in subs:
            task_idx = sub["problem_id"]
            if task_idx not in problem_status:
                problem_status[task_idx] = {'ac_time': -1, 'penalties': 0, 'point': 0}
            
            p_data = problem_status[task_idx]
            if p_data['ac_time'] != -1: continue 
            
            if sub["result"] == "AC":
                # 【テスト用】過去の提出の場合は、開始から1秒後に爆速ACしたことにするにゃ！
                elapsed_sec = sub["epoch_second"] - start_epoch
                if elapsed_sec < 0: elapsed_sec = 1 
                
                p_data['ac_time'] = elapsed_sec
                p_data['point'] = sub["point"]
                total_score += sub["point"]
                last_ac_time = max(last_ac_time, elapsed_sec)
                total_penalties += p_data['penalties']
            elif sub["result"] not in ["CE", "IE", "WJ", "WR"]:
                p_data['penalties'] += 1

        elapsed_penalty_sec = last_ac_time + (total_penalties * 300)
        
        v_rank = 1
        for s in standings["StandingsData"]:
            s_score = s["TotalResult"]["Score"] / 100
            s_elapsed = s["TotalResult"]["Elapsed"] / 1000000000
            if s_score > total_score: v_rank += 1
            elif s_score == total_score and s_elapsed < elapsed_penalty_sec: v_rank += 1
            
        # 【バグ修正】KeyErrorを防止！安全に.get()で取得する
        perf = "-"
        for r in results:
            if r.get("Rank") == v_rank or r.get("Place") == v_rank:
                perf = r.get("Performance", "-")
                break

        member = channel.guild.get_member(int(d_id))
        display_name = member.display_name if member else user

        ranking_data.append({
            "user": user, "display": display_name,
            "score": int(total_score), "time": elapsed_penalty_sec,
            "rank": v_rank, "perf": perf,
            "status": problem_status, "penalties": total_penalties
        })

    ranking_data.sort(key=lambda x: (-x["score"], x["time"]))

    msg_lines = [f"🏆 **{cid.upper()} バチャコン 最終結果** 🏆"]
    for i, data in enumerate(ranking_data):
        m, s = divmod(data["time"], 60)
        time_str = f"{int(m)}:{int(s):02d}"
        
        task_strs = []
        last_ac_index = -1
        for j, t in enumerate(tasks):
            if t in data["status"] and data["status"][t]["ac_time"] != -1: last_ac_index = j

        for j, t in enumerate(tasks):
            if j > last_ac_index: break 
            
            p_data = data["status"].get(t, {'ac_time': -1, 'penalties': 0})
            pens = p_data["penalties"]
            
            if p_data["ac_time"] != -1:
                cross = "" if pens == 0 else ("❌" * pens if pens < 3 else f"❌x{pens}")
                tm, ts = divmod(p_data["ac_time"], 60)
                task_strs.append(f"{t}: {cross}✅({int(tm)}:{int(ts):02d})")
            else:
                cross = "-" if pens == 0 else ("❌" * pens if pens < 3 else f"❌x{pens}")
                task_strs.append(f"{t}: {cross}")

        task_line = " | ".join(task_strs) if task_strs else "提出なし"
        
        msg_lines.append(f"**{i+1}({data['rank']})位**: {data['user']}@{data['display']}  {data['score']}pts - {time_str}({data['penalties']}) perf : **{data['perf']}**")
        msg_lines.append(f"  [{task_line}]")

    await channel.send("\n".join(msg_lines))

# ==========================================
# Renderのお昼寝防止用ダミーサーバー
# ==========================================
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()

bot.run(TOKEN)
