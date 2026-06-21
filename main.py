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
import math
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bs4 import BeautifulSoup
from aiohttp import web

# ==========================================
# 設定とデータベース
# ==========================================
TOKEN = os.getenv("DISCORD_TOKEN") 
DATA_CHANNEL_ID = int(os.getenv("DATA_CHANNEL_ID", 0))
REVEL_SESSION = os.getenv("REVEL_SESSION")
PORT = int(os.environ.get("PORT", 8080))

users_data = {}
history_data = []
vcons_data = {} # 予約されたバチャコンのデータ保存用
data_message_id = None 

scheduler = AsyncIOScheduler()
vcon_sessions = {} # message_id -> set(user_id)
JST = timezone(timedelta(hours=9))

# WebSocketで接続しているブラウザのリスト
connected_clients = set()

# ==========================================
# データ保存・復元処理
# ==========================================
async def load_data_from_channel(bot):
    global users_data, history_data, vcons_data, data_message_id
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
                vcons_data = data.get("vcons", {})
                data_message_id = msg.id
                print("Discordチャンネルからデータを復元したにゃ！")
                
                # 未来のバチャコンがあればタイマーを復元！
                now = datetime.datetime.now(JST)
                for msg_id_str, v_data in vcons_data.items():
                    start_dt = datetime.datetime.fromisoformat(v_data["start_time"])
                    channel_id = v_data["channel_id"]
                    msg_id = int(msg_id_str)
                    
                    if msg_id not in vcon_sessions:
                        vcon_sessions[msg_id] = set(v_data.get("participants", []))
                    
                    if start_dt > now:
                        # まだ開始前なら決定タイマー等を復元
                        run_time = start_dt - datetime.timedelta(minutes=90)
                        if run_time > now:
                            scheduler.add_job(decide_vcontest, 'date', run_date=run_time, args=[channel_id, msg_id, start_dt, v_data.get("contest_id")])
                        elif start_dt > now:
                            # 90分前は過ぎているが開始前の場合、2分後に決定
                            scheduler.add_job(decide_vcontest, 'date', run_date=now+datetime.timedelta(minutes=2), args=[channel_id, msg_id, start_dt, v_data.get("contest_id")])
                return
            except Exception as e: 
                print(f"データパースエラー: {e}")

    await save_data_to_channel(bot)

async def save_data_to_channel(bot):
    global data_message_id
    if DATA_CHANNEL_ID == 0: return
    channel = bot.get_channel(DATA_CHANNEL_ID)
    if not channel: return

    # 参加者リストをセットからリストに変換して保存
    for msg_id, participants in vcon_sessions.items():
        if str(msg_id) in vcons_data:
            vcons_data[str(msg_id)]["participants"] = list(participants)

    data = {"users": users_data, "history": history_data, "vcons": vcons_data}
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
# UIコンポーネント (遅刻参加対応)
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
            await interaction.response.send_message("参加登録したにゃ！", ephemeral=True)
            
        await save_data_to_channel(bot) # 状態を永続化

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
    print(f'ログインしましたにゃ: {bot.user.name}')
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
            log_text += f"**[1. 本家 standings/json]**\nStatus: 200\n **Cookie突破したにゃ！**\n\n"
        else: log_text += f"**[1. 本家 standings/json]**\nStatus: {s_res.status_code}\n\n"
    except Exception as e: log_text += f"Error: {e}\n\n"

    try:
        url = f"https://atcoder.jp/contests/{contest_id}/submissions?f.User={user_id}"
        sub_res = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, timeout=10)
        soup = BeautifulSoup(sub_res.text, 'html.parser')
        rows = soup.select('table tbody tr')
        log_text += f"**[2. 本家 提出スクレイピング]**\nStatus: {sub_res.status_code}\n"
        if rows: log_text += f"提出一覧を取得できたにゃ！**\n"
        else: log_text += "取得した提出行数: 0\n"
    except Exception as e: log_text += f"Error: {e}\n"
    await interaction.followup.send(log_text)

@bot.tree.command(name="test_api", description="順位表とパフォのAPIが取れるかテストするにゃ")
@app_commands.describe(contest_id="コンテストID (例: abc158)")
async def test_api(interaction: discord.Interaction, contest_id: str):
    await interaction.response.defer()
    log_text = f"🔍 **API取得テスト ({contest_id.upper()})**\n\n"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    cookies = {'REVEL_SESSION': REVEL_SESSION} if REVEL_SESSION else {}

    # 1. standings/json (順位表)
    try:
        s_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/contests/{contest_id}/standings/json", headers=headers, cookies=cookies, timeout=10)
        log_text += f"**[1. standings/json (順位表)]**\nHTTP Status: {s_res.status_code}\n"
        s_data = s_res.json()
        log_text += f"✅ JSON変換成功！ (Task数: {len(s_data.get('TaskInfo', []))})\n\n"
    except Exception as e: 
        log_text += f"❌ エラー: `{type(e).__name__}` ({e})\n\n"

    # 2. results/json (パフォーマンス用)
    try:
        r_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/contests/{contest_id}/results/json", headers=headers, cookies=cookies, timeout=10)
        log_text += f"**[2. results/json (パフォ用)]**\nHTTP Status: {r_res.status_code}\n"
        r_data = r_res.json()
        log_text += f"✅ JSON変換成功！ (データ数: {len(r_data)})\n\n"
    except Exception as e: 
        log_text += f"❌ エラー: `{type(e).__name__}` ({e})\n\n"

    await interaction.followup.send(log_text)

# 👑 【NEW】オプション追加版 vcontest
@bot.tree.command(name="vcontest", description="バチャコンの募集を開始するにゃ")
@app_commands.describe(
    start_time="開始日時 (例: 2026-06-18 21:00)",
    contest_id="コンテスト回を固定する場合に入力するにゃ (例: abc250)",
    comment="募集メッセージにコメントを添えるにゃ \nあなたのメッセージセンスが問われるにゃ～"
)
async def vcontest(interaction: discord.Interaction, start_time: str, contest_id: str = None, comment: str = None):
    try:
        dt = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    except ValueError:
        return await interaction.response.send_message("日時のフォーマットが違うにゃ！ `2026-06-18 21:00` のように入力してにゃ", ephemeral=True)
    
    now = datetime.datetime.now(JST)
    run_time = dt - datetime.timedelta(minutes=90) 
    
    if run_time < now:
        if dt < now:
            return await interaction.response.send_message("開始時間が過去だにゃ。\n時間は過去には巻き戻せないにゃ～", ephemeral=True)
        run_time = now + datetime.timedelta(minutes=2)

    comment_text = f"💬 {comment}\n\n" if comment else ""
    contest_text = f"👉 開催予定: **{contest_id.upper()}**\n" if contest_id else f"(*{run_time.strftime('%H:%M')} に、ねこが最適な回を自動決定するにゃ*)\n"

    base_text = (
        f"📢 **バチャコン募集！**\n"
        f"{comment_text}"
        f"開始時間: **{dt.strftime('%Y-%m-%d %H:%M')}**\n"
        f"{contest_text}\n"
        f"参加する人は下のボタンを押すんだにゃ！"
    )

    await interaction.response.send_message(f"{base_text}\n\n**【現在の参加者】**\nまだいないにゃ", view=VconJoinView())
    msg = await interaction.original_response()
    
    # 予約データを保存
    vcon_sessions[msg.id] = set()
    vcons_data[str(msg.id)] = {
        "channel_id": interaction.channel_id,
        "start_time": dt.isoformat(),
        "contest_id": contest_id.lower() if contest_id else None,
        "participants": []
    }
    await save_data_to_channel(bot)

    # 決定処理の予約
    scheduler.add_job(decide_vcontest, 'date', run_date=run_time, args=[interaction.channel_id, msg.id, dt, contest_id])

# ==========================================
# コンテスト決定
# ==========================================
async def decide_vcontest(channel_id, message_id, start_dt, force_contest_id=None):
    channel = bot.get_channel(channel_id)
    if not channel: return
    
    chosen_cid = force_contest_id

    if not chosen_cid:
        participants_discord_ids = list(vcon_sessions.get(message_id, set()))
        if not participants_discord_ids:
            return await channel.send("参加者がいなかったので、今回のバチャコンは自動中止になったにゃ！")

        atcoder_ids = [users_data[d_id] for d_id in participants_discord_ids]
        status_msg = await channel.send(f"**コンテストの決定処理を開始するにゃ！**\n(参加者: {', '.join(atcoder_ids)})\n`データ取得中にゃ...`")

        try: contests_data = (await asyncio.to_thread(requests.get, "https://kenkoooo.com/atcoder/resources/contests.json")).json()
        except: return await channel.send("APIの取得に失敗したにゃ...")
            
        target_contests = set(c["id"] for c in contests_data if c["id"].startswith("abc") and int(c["id"][3:6]) >= 126 and c["id"] not in history_data)
        if not target_contests: return await channel.send("対象となるコンテストがもうないにゃ！")
        
        user_ac_data = {} 
        for i, user in enumerate(atcoder_ids):
            await status_msg.edit(content=f"**コンテストの決定処理を開始するにゃ！**\n`データ取得中にゃ... ({i+1}/{len(atcoder_ids)}人完了)`")
            user_ac_data[user] = {}
            try:
                url = f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={user}"
                await asyncio.sleep(1.0) 
                subs = (await asyncio.to_thread(requests.get, url)).json()
                for sub in subs:
                    if sub["result"] == "AC" and sub["contest_id"] in target_contests:
                        user_ac_data[user].setdefault(sub["contest_id"], set()).add(sub["problem_id"].split("_")[-1])
            except: pass

        await status_msg.edit(content=f" `全員のデータを取得したにゃ！最適な回を計算中にゃ～`")

        valid_contests = [] 
        for cid in target_contests:
            is_6_prob = (126 <= int(cid[3:6]) <= 211)
            exclude, total_score, score_4_over = False, 0, 0
            for user in atcoder_ids:
                ac_set = user_ac_data[user].get(cid, set())
                if len(ac_set) >= 5 or any(len(idx)>1 or idx>='f' for idx in ac_set):
                    exclude = True; break
                score = 0
                if is_6_prob: score += 1.5 if 'd' in ac_set else 0; score += 4 if 'e' in ac_set else 0
                else: score += 1 if 'd' in ac_set else 0; score += 3 if 'e' in ac_set else 0
                total_score += score
                if score >= 4: score_4_over += 1
            if not exclude and (score_4_over / len(atcoder_ids)) < 0.35:
                valid_contests.append((cid, total_score))

        if not valid_contests: return await channel.send("ちょうどいい難易度の回が見つからなかったにゃ...")

        valid_contests.sort(key=lambda x: x[1])
        scores = [(cid, score + 1) for cid, score in valid_contests[:30]]
        min_score_6 = scores[0][1] ** 6
        weights = [min_score_6 / (s ** 6) for _, s in scores]
        chosen_cid = random.choices([c for c, _ in scores], weights=weights, k=1)[0]
        
        history_data.append(chosen_cid)
        await save_data_to_channel(bot)
        await status_msg.delete()

    await channel.send(
        f"**今回のバチャコンの回が決定したにゃ！！**\n👉 **{chosen_cid.upper()}** (https://atcoder.jp/contests/{chosen_cid})\n"
        f"開始時間は **{start_dt.strftime('%H:%M')}** だにゃ！\n"
        f"**ライブ順位表はここにゃ:**  https://virtual-contest-cat.onrender.com/\n"
        #f"*(※遅れて参加ボタンを押しても順位表に反映されるにゃ！)*"
    )

    # ライブ順位表タスクを開始時刻に予約
    scheduler.add_job(trigger_live_standings, 'date', run_date=start_dt, args=[channel_id, message_id, chosen_cid, start_dt])

    # 最終結果発表を終了1分後(101分後)に予約
    end_time = start_dt + datetime.timedelta(minutes=101)
    scheduler.add_job(aggregate_vcontest, 'date', run_date=end_time, args=[channel_id, message_id, chosen_cid, start_dt])

# ==========================================
# 🟢 ライブ順位表 & WebSocket処理
# ==========================================
def trigger_live_standings(channel_id, message_id, cid, start_dt):
    bot.loop.create_task(live_standings_loop(channel_id, message_id, cid, start_dt))

async def live_standings_loop(channel_id, message_id, cid, start_dt):
    import traceback # エラー解析用に追加
    channel = bot.get_channel(channel_id)
    if channel: await channel.send(f"🟢 **{cid.upper()} ライブ順位表が起動したにゃ！**\n👉 URL:  https://virtual-contest-cat.onrender.com/\n")

    start_epoch = int(start_dt.timestamp())
    end_dt = start_dt + datetime.timedelta(minutes=100)
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    cookies = {'REVEL_SESSION': REVEL_SESSION} if REVEL_SESSION else {}

    try:
        s_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/contests/{cid}/standings/json", headers=headers, cookies=cookies, timeout=20)
        standings = s_res.json()
        tasks = [t["Assignment"] for t in standings.get("TaskInfo", [])] 
    except Exception as e:
        if channel: await channel.send(f"⚠️ 順位表の初期化に失敗したにゃ...(`{e}`)")
        return

    try:
        r_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/contests/{cid}/results/json", headers=headers, cookies=cookies, timeout=20)
        results = r_res.json()
    except:
        results = []

    user_ratings = {}
    
    # 100分間ループ
    while datetime.datetime.now(JST) < end_dt:
        try: # 👑 ループ全体をエラー監視！
            discord_ids = list(vcon_sessions.get(message_id, set()))
            interval = max(10, len(discord_ids) * 1)

            ranking_data = []
            all_subs_data = []

            for d_id in discord_ids:
                user = users_data.get(d_id)
                if not user: continue
                
                if user not in user_ratings:
                    try:
                        h_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/users/{user}/history/json", headers=headers, cookies=cookies, timeout=10)
                        if h_res.status_code == 200 and h_res.json(): user_ratings[user] = h_res.json()[-1].get("NewRating", 0)
                        else: user_ratings[user] = 0
                    except: user_ratings[user] = 0

                subs = []
                url = f"https://atcoder.jp/contests/{cid}/submissions?f.User={user}"
                await asyncio.sleep(1.0)
                try:
                    res = await asyncio.to_thread(requests.get, url, headers=headers, cookies=cookies, timeout=10)
                    soup = BeautifulSoup(res.text, 'html.parser')
                    rows = soup.select('table tbody tr')
                    if rows:
                        for row in rows:
                            cells = row.find_all('td')
                            if len(cells) < 8: continue
                            time_tag = cells[0].find('time')
                            if not time_tag: continue
                            sub_epoch = int(datetime.datetime.strptime(time_tag.text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST).timestamp())
                            
                            if start_epoch <= sub_epoch <= int(datetime.datetime.now(JST).timestamp()):
                                task_link = cells[1].find('a')
                                task_idx = task_link.get('href', '').split('_')[-1].upper() if task_link else 'A'
                                score_text = cells[4].text.strip()
                                score = float(score_text) if score_text.replace('.', '', 1).isdigit() else 0
                                result_label = cells[6].find('span')
                                result = result_label.text.strip() if result_label else "WJ"
                                sub_id = row.get('data-id') or str(sub_epoch) # 簡易ID
                                
                                subs.append({"epoch_second": sub_epoch, "problem_id": task_idx, "result": result, "point": score, "id": sub_id})
                                all_subs_data.append({
                                    "id": f"{user}_{sub_id}", "user": user, "user_rate": user_ratings[user],
                                    "prob": task_idx, "prob_title": f"Problem {task_idx}", "time": sub_epoch - start_epoch,
                                    "point": score, "result": result, "epoch": sub_epoch
                                })
                except Exception as e:
                    print(f"提出取得エラー: {e}")

                subs.sort(key=lambda x: x["epoch_second"])
                
                problem_status = {}
                total_score = last_ac_time = total_penalties = 0

                for sub in subs:
                    task_idx = sub["problem_id"]
                    if task_idx not in problem_status: problem_status[task_idx] = {'ac_time': -1, 'penalties': 0, 'point': 0}
                    p_data = problem_status[task_idx]
                    if p_data['ac_time'] != -1: continue 
                    
                    if sub["result"] == "AC":
                        elapsed_sec = sub["epoch_second"] - start_epoch
                        p_data['ac_time'] = elapsed_sec
                        p_data['point'] = sub["point"]
                        total_score += sub["point"]
                        last_ac_time = max(last_ac_time, elapsed_sec)
                        total_penalties += p_data['penalties']
                    elif sub["result"] not in ["CE", "IE", "WJ", "WR"]:
                        p_data['penalties'] += 1

                elapsed_penalty_sec = last_ac_time + (total_penalties * 300)
                
                v_rank = 1
                for s in standings.get("StandingsData", []):
                    s_score = s["TotalResult"]["Score"] / 100
                    s_elapsed = s["TotalResult"]["Elapsed"] / 1000000000
                    if s_score > total_score: v_rank += 1
                    elif s_score == total_score and s_elapsed < elapsed_penalty_sec: v_rank += 1
                    
                perf = "-"
                for r in results:
                    if r.get("Rank") == v_rank or r.get("Place") == v_rank:
                        perf = r.get("Performance", "-")
                        break

                # 👑 ここを安全な書き方に修正！
                member = channel.guild.get_member(int(d_id)) if channel and hasattr(channel, 'guild') else None
                display_name = member.display_name if member else user

                ranking_data.append({
                    "id": user, "display": display_name, "score": int(total_score), "time": elapsed_penalty_sec,
                    "v_rank": v_rank, "perf": perf, "old_rate": user_ratings[user], "rate": user_ratings[user], 
                    "status": problem_status, "penalties": total_penalties
                })

            # レート計算
            for data in ranking_data:
                try:
                    perf_int = int(data["perf"])
                    if data["old_rate"] > 0:
                        x_new = (2.0 ** (data["old_rate"] / 400.0)) * 0.9 + (2.0 ** (perf_int / 400.0)) * 0.1
                        data["rate"] = int(round(400.0 * math.log2(x_new)))
                except: pass

            all_subs_data.sort(key=lambda x: x["epoch"])
            
            # 🌐 Web側にデータを送信
            now_dt = datetime.datetime.now(JST)
            elapsed_sec = int((now_dt - start_dt).total_seconds())
            ws_data = {
                "type": "update", "status": "running", "elapsed": elapsed_sec, "total": 100 * 60,
                "tasks": tasks, "standings": ranking_data, "submissions": all_subs_data, "blink_user": None
            }
            
            for ws in list(connected_clients):
                try: await ws.send_json(ws_data)
                except Exception: connected_clients.remove(ws)
            
            await asyncio.sleep(interval)
            
        # 👑 致命的なエラーが起きたらDiscordに通知して、10秒後にリトライする！
        except Exception as e:
            err_msg = traceback.format_exc()
            print(f"内部エラー発生: {err_msg}")
            if channel: await channel.send(f"⚠️ 順位表の更新中にエラーが起きたにゃ！\n```py\n{e}\n```\n`10秒後に再試行するにゃ...`")
            await asyncio.sleep(10)

    # 終了シグナルをWebに送信
    for ws in list(connected_clients):
        try: await ws.send_json({"type": "update", "status": "finished", "elapsed": 6000, "total": 6000, "tasks": tasks, "standings": ranking_data, "submissions": all_subs_data})
        except: pass

# ==========================================
# 🏁 最終結果：自動集計・パフォ＆レート計算
# ==========================================
async def aggregate_vcontest(channel_id, message_id, cid, start_dt):
    channel = bot.get_channel(channel_id)
    if not channel: return
    await channel.send(f"🏁 **{cid.upper()} バチャコン終了にゃ！！**\n`結果とパフォーマンスを持ってくるにゃ...`")

    # 遅刻者対応！
    discord_ids = list(vcon_sessions.get(message_id, set()))
    
    start_epoch = int(start_dt.timestamp())
    end_epoch = start_epoch + 100 * 60

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
        
        current_rating = 0
        try:
            h_url = f"https://atcoder.jp/users/{user}/history/json"
            await asyncio.sleep(1.0)
            h_res = await asyncio.to_thread(requests.get, h_url, headers=headers, cookies=cookies, timeout=10)
            if h_res.status_code == 200 and h_res.json():
                current_rating = h_res.json()[-1].get("NewRating", 0)
        except: pass

        subs = []
        for page in range(1, 4):
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

                if start_epoch <= sub_epoch <= end_epoch:
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
        total_score = last_ac_time = total_penalties = 0

        for sub in subs:
            task_idx = sub["problem_id"]
            if task_idx not in problem_status: problem_status[task_idx] = {'ac_time': -1, 'penalties': 0, 'point': 0}
            p_data = problem_status[task_idx]
            if p_data['ac_time'] != -1: continue 
            
            if sub["result"] == "AC":
                elapsed_sec = sub["epoch_second"] - start_epoch
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
            "current_rating": current_rating,
            "status": problem_status, "penalties": total_penalties
        })

    ranking_data.sort(key=lambda x: (-x["score"], x["time"]))

    msg_lines = [f" **{cid.upper()} バチャコン 最終結果** \n*(※綺麗な順位表の画像はWeb版の 📥 ボタンから保存して共有できるにゃ！)*"]
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
        
        perf = data["perf"]
        current_rating = data["current_rating"]
        rating_str = ""
        try:
            perf_int = int(perf)
            if current_rating > 0:
                x_old = 2.0 ** (current_rating / 400.0)
                x_perf = 2.0 ** (perf_int / 400.0)
                x_new = x_old * 0.9 + x_perf * 0.1
                new_rate = int(round(400.0 * math.log2(x_new)))
                diff = new_rate - current_rating
                sign = "+" if diff >= 0 else ""
                rating_str = f" | Rate: {current_rating} → **{new_rate}** ({sign}{diff})"
        except: pass

        msg_lines.append(f"**{i+1}({data['rank']})位**: {data['user']}@{data['display']}  {data['score']}pts - {time_str}({data['penalties']}) perf : **{perf}**{rating_str}")
        msg_lines.append(f"  [{task_line}]")

    await channel.send("\n".join(msg_lines))
    
    # 使い終わった予定を削除
    if str(message_id) in vcons_data:
        del vcons_data[str(message_id)]
        await save_data_to_channel(bot)

# ==========================================
# 🌐 Web & WebSocket サーバー (aiohttp)
# ==========================================
async def handle_index(request):
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            html = f.read()
            # サーバーのURLを自動で埋め込む
            ws_url = "wss://" + request.host + "/ws" if "onrender.com" in request.host else "ws://" + request.host + "/ws"
            html = html.replace("/* WEBSOCKET_INJECTION_POINT */", f"var WS_URL = '{ws_url}';")
        return web.Response(text=html, content_type='text/html')
    except Exception as e:
        return web.Response(text=f"index.html が見つからないにゃ... ({e})", status=404)

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    connected_clients.add(ws)
    print("Web画面が繋がったにゃ！")
    try:
        async for msg in ws: pass
    finally:
        connected_clients.remove(ws)
        print("Web画面が閉じたにゃ...")
    return ws

async def web_server_runner():
    app = web.Application()
    app.add_routes([web.get('/', handle_index), web.get('/ws', websocket_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"Webサーバーをポート {PORT} で起動したにゃ！")

# Discord Botの起動とWebサーバーの並列起動
async def main():
    async with bot:
        bot.loop.create_task(web_server_runner())
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
