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
import io
import bisect 
import aiohttp
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
vcons_data = {} 
data_message_id = None 

vcon_sessions = {} 
JST = timezone(timedelta(hours=9))
scheduler = AsyncIOScheduler(timezone=JST)

connected_clients = {}
last_ws_data = {}

# ==========================================
# データ保存・復元処理
# ==========================================
async def load_data_from_channel(bot):
    global users_data, history_data, vcons_data, data_message_id
    if DATA_CHANNEL_ID == 0: return
    channel = bot.get_channel(DATA_CHANNEL_ID)
    if not channel: return

    async for msg in channel.history(limit=50):
        if msg.author == bot.user:
            try:
                json_str = None
                if msg.attachments and msg.attachments[0].filename.endswith(".json"):
                    json_bytes = await msg.attachments[0].read()
                    json_str = json_bytes.decode('utf-8')
                elif "```json" in msg.content:
                    json_str = msg.content.split("```json")[1].split("```")[0].strip()
                
                if json_str:
                    data = json.loads(json_str)
                    users_data = data.get("users", {})
                    history_data = data.get("history", [])
                    vcons_data = data.get("vcons", {})
                    data_message_id = msg.id
                    print("Discordチャンネルからデータを復元したにゃ！")
                    
                    now = datetime.datetime.now(JST)
                    for msg_id_str, v_data in vcons_data.items():
                        start_dt = datetime.datetime.fromisoformat(v_data["start_time"])
                        channel_id = v_data["channel_id"]
                        msg_id = int(msg_id_str)
                        
                        if msg_id not in vcon_sessions:
                            vcon_sessions[msg_id] = set(v_data.get("participants", []))
                        
                        if start_dt > now:
                            run_time = start_dt - datetime.timedelta(minutes=90)
                            if run_time > now:
                                scheduler.add_job(decide_vcontest, 'date', run_date=run_time, args=[channel_id, msg_id, start_dt, v_data.get("contest_id")])
                            elif start_dt > now:
                                scheduler.add_job(decide_vcontest, 'date', run_date=now+datetime.timedelta(minutes=2), args=[channel_id, msg_id, start_dt, v_data.get("contest_id")])
                        else:
                            duration = v_data.get("duration")
                            chosen_cid = v_data.get("chosen_cid")
                            if duration and chosen_cid:
                                end_time = start_dt + datetime.timedelta(seconds=duration)
                                if now < end_time:
                                    print(f"進行中のバチャコン({chosen_cid})の監視を再開するにゃ！")
                                    scheduler.add_job(live_standings_loop, 'date', run_date=now+datetime.timedelta(seconds=5), args=[channel_id, msg_id, chosen_cid, start_dt, duration])
                                    scheduler.add_job(aggregate_vcontest, 'date', run_date=end_time + datetime.timedelta(seconds=60), args=[channel_id, msg_id, chosen_cid, start_dt, duration])

                    return
            except Exception as e: 
                print(f"データパースエラー: {e}")

    await save_data_to_channel(bot)

async def save_data_to_channel(bot):
    global data_message_id
    if DATA_CHANNEL_ID == 0: return
    channel = bot.get_channel(DATA_CHANNEL_ID)
    if not channel: return

    for msg_id, participants in vcon_sessions.items():
        if str(msg_id) in vcons_data:
            vcons_data[str(msg_id)]["participants"] = list(participants)

    data = {"users": users_data, "history": history_data, "vcons": vcons_data}
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    
    file = discord.File(io.BytesIO(json_str.encode('utf-8')), filename="data.json")
    content_text = "なんと、データ保存用ファイル"

    if data_message_id:
        try:
            msg = await channel.fetch_message(data_message_id)
            await msg.edit(content=content_text, attachments=[file])
            return
        except discord.NotFound: pass

    msg = await channel.send(content=content_text, file=file)
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
            await interaction.response.send_message("参加登録したにゃ！", ephemeral=True)
            
        await save_data_to_channel(bot) 

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

#色付けBOTを読み込んであげる
async def setup_hook():
    # 既存のボタンUIの登録処理
    bot.add_view(VconJoinView())

    # cogsフォルダ内のファイルを読み込む
    if os.path.exists("./cogs"):
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                try:
                    await bot.load_extension(f"cogs.{filename[:-3]}")
                    print(f"✅ Cog読み込み成功: {filename}")
                except Exception as e:
                    print(f"❌ Cog読み込み失敗 ({filename}): {e}")
    
    # スラッシュコマンドをDiscordに同期
    await bot.tree.sync()
    print("✅ スラッシュコマンドの同期完了")

bot.setup_hook = setup_hook

@bot.event
async def on_ready():
    print(f'ログインしたにゃ: {bot.user.name}')
    await load_data_from_channel(bot)
    if not scheduler.running: scheduler.start()
    await bot.tree.sync()

@bot.tree.command(name="register", description="自分のAtCoder IDをBotに登録するにゃ")
@app_commands.describe(atcoder_id="あなたのAtCoder IDを入力してにゃ")
async def register(interaction: discord.Interaction, atcoder_id: str):
    users_data[str(interaction.user.id)] = atcoder_id
    await save_data_to_channel(bot)
    await interaction.response.send_message(f"{interaction.user.mention} さんのAtCoder IDを `{atcoder_id}` として登録したにゃ～", ephemeral=False)

@bot.tree.command(name="vcontest", description="バチャコンの募集を開始するにゃ")
@app_commands.describe(
    start_time="開始日時 (例: 2026-06-18 21:00)",
    contest_id="コンテスト回を固定する場合に入力するにゃ (例: abc250)",
    type="コンテストの種類を指定するにゃ (abc, arc, agc, awc, ahc_short, ahc_long)",
    comment="募集メッセージにコメントを添えるにゃ \nあなたのメッセージセンスが問われるにゃ～"
)
async def vcontest(interaction: discord.Interaction, start_time: str, contest_id: str = None, type: str = "abc", comment: str = None):
    type = type.lower()
    if type not in ["abc", "arc", "agc", "awc", "ahc_short", "ahc_long"]:
        return await interaction.response.send_message("typeは abc, arc, agc, awc, ahc_short, ahc_long のいずれかを指定してにゃ！", ephemeral=True)

    try:
        dt = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    except ValueError:
        return await interaction.response.send_message("日時のフォーマットが違うにゃ！ `2026-06-18 21:00` のように入力してにゃ", ephemeral=True)

    now = datetime.datetime.now(JST)
    # 過去なら爆破
    if dt < now:
        return await interaction.response.send_message("開始時間が過去だにゃ。\n時間は過去には巻き戻せないにゃ～", ephemeral=True)

    # 現在から開始時刻（dt）までの残り猶予（分）を計算
    time_left = (dt - now).total_seconds() / 60.0

    if time_left >= 75:
        # 猶予75分以上:
        run_time_90 = dt - datetime.timedelta(minutes=90)
        run_time_60_from_now = now + datetime.timedelta(minutes=60)

        # どちらか未来の方（遅い方）を採用
        run_time = max(run_time_90, run_time_60_from_now)
    
        # ※もし「常に開始15分前」に固定したい場合は、これらを消して以下1行にしてください
        # run_time = dt - datetime.timedelta(minutes=15)

    elif 30 <= time_left < 75:
        # 猶予30分以上～75分未満:
        # 開始時刻猶予（15分）を死守し、決定処理猶予（60分）の方を削る
        run_time = dt - datetime.timedelta(minutes=15)

    elif 4 <= time_left < 30:
        # 猶予4分以上～30分未満:
        # 開始時刻猶予と決定処理猶予を半分ずつ割り当てる
        run_time = now + datetime.timedelta(minutes=time_left / 2)

    else:
        # ④ 猶予4分未満:
        # 開始時刻の1分前に決定処理を行う
        run_time = dt - datetime.timedelta(minutes=1)

        # 猶予が1分未満（例えば30秒後）で、すでに「1分前」が過去になってしまう場合のフェイルセーフ
        if run_time < now:
            run_time = now

    comment_text = f"💬 {comment}\n\n" if comment else ""
    
    if contest_id:
        contest_text = f"👉 開催予定: **{contest_id.upper()}**\n"
    else:
        contest_text = f"👉 対象コンテスト: **{type.upper()}**\n(*{run_time.strftime('%H:%M')} に、ねこが最適な回を自動決定するにゃ*)\n"

    base_text = (
        f"📢 **バチャコン募集！**\n"
        f"{comment_text}"
        f"開始時間: **{dt.strftime('%Y-%m-%d %H:%M')}**\n"
        f"{contest_text}\n"
        f"参加する人は下のボタンを押すんだにゃ！"
    )

    await interaction.response.send_message(f"{base_text}\n\n**【現在の参加者】**\nまだいないにゃ", view=VconJoinView())
    msg = await interaction.original_response()
    
    vcon_sessions[msg.id] = set()
    vcons_data[str(msg.id)] = {
        "channel_id": interaction.channel_id,
        "start_time": dt.isoformat(),
        "contest_id": contest_id.lower() if contest_id else None,
        "type": type,
        "participants": []
    }
    await save_data_to_channel(bot)

    scheduler.add_job(decide_vcontest, 'date', run_date=run_time, args=[interaction.channel_id, msg.id, dt, contest_id])

@bot.tree.command(name="vlist", description="予定されているバチャコンの一覧を表示するにゃ")
async def vlist(interaction: discord.Interaction):
    if not vcons_data:
        return await interaction.response.send_message("現在予定されているバチャコンはないにゃ！", ephemeral=True)
    
    embed = discord.Embed(
        title="📋 バチャコン予定一覧", 
        color=discord.Color.blue()
    )
    
    for msg_id_str, v_data in vcons_data.items():
        msg_id = int(msg_id_str)
        ch_id = v_data.get("channel_id")
        
        # チャンネルのメンション表示
        channel = bot.get_channel(ch_id)
        ch_mention = channel.mention if channel else f"チャンネルID: {ch_id}"
        
        # 開始時間のフォーマット整形
        start_time_raw = v_data.get("start_time", "")
        try:
            dt = datetime.datetime.fromisoformat(start_time_raw)
            time_str = dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            time_str = start_time_raw

        # コンテスト情報の構築 (vcontestの表示に準拠)
        contest_id = v_data.get("contest_id")
        ctype = v_data.get("type", "abc").upper()
        if contest_id:
            contest_info = f"👉 開催予定: **{contest_id.upper()}**"
        else:
            contest_info = f"👉 対象コンテスト: **{ctype}**"

        # --- 👥 募集メッセージと同じロジックで参加者文字列を作成 ---
        participant_ids = vcon_sessions.get(msg_id, set())
        participants_mentions = [f"<@{uid}>" for uid in participant_ids]
        join_text = " ".join(participants_mentions) if participants_mentions else "まだいないにゃ"

        # Embedにセット
        embed.add_field(
            name=f"📍 チャンネル: {ch_mention}",
            value=(
                f"⏰ **開始時間:** {time_str}\n"
                f"{contest_info}\n\n"
                f"**【現在の参加者】**\n{join_text}"
            ),
            inline=False
        )
    
    # 実行した人にだけ表示（ephemeral=True）
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ==========================================
# コンテスト決定
# ==========================================
async def decide_vcontest(channel_id, message_id, start_dt, force_contest_id=None):
    channel = bot.get_channel(channel_id)
    if not channel: return
    
    chosen_cid = force_contest_id
    ctype = vcons_data.get(str(message_id), {}).get("type", "abc")

    if not chosen_cid:
        participants_discord_ids = list(vcon_sessions.get(message_id, set()))
        if not participants_discord_ids:
            if str(message_id) in vcons_data:
                del vcons_data[str(message_id)]
                await save_data_to_channel(bot)
            return await channel.send("参加者がいなかったので、今回のバチャコンは自動中止になったにゃ！")

        atcoder_ids = [users_data[d_id] for d_id in participants_discord_ids]
        status_msg = await channel.send(f"**コンテストの決定処理を開始するにゃ！**\n(参加者: {', '.join(atcoder_ids)})\n`データ取得中にゃ...`")

        try: 
            contests_data = (await asyncio.to_thread(requests.get, "https://kenkoooo.com/atcoder/resources/contests.json")).json()
        except: return await channel.send("APIの取得に失敗したにゃ...")
            
        target_contests = set()
        for c in contests_data:
            cid = c["id"]
            if cid in history_data: continue
            dur = c.get("duration_second", 0)

            if ctype == "abc":
                if cid.startswith("abc") and cid[3:6].isdigit() and int(cid[3:6]) >= 126:
                    target_contests.add(cid)
            elif ctype == "arc":
                if cid.startswith("arc") and cid[3:6].isdigit() and int(cid[3:6]) >= 58:
                    target_contests.add(cid)
            elif ctype == "agc":
                if cid.startswith("agc") and cid[3:6].isdigit() and int(cid[3:6]) >= 10:
                    target_contests.add(cid)
            elif ctype == "awc":
                if cid.startswith("awc") and cid[3:7].isdigit() and int(cid[3:7]) % 100 != 0:
                    target_contests.add(cid)
            elif ctype == "ahc_short":
                if cid.startswith("ahc") and cid[3:6].isdigit() and int(cid[3:6]) >= 10 and dur <= 86400:
                    target_contests.add(cid)
            elif ctype == "ahc_long":
                if cid.startswith("ahc") and cid[3:6].isdigit() and int(cid[3:6]) >= 10 and dur > 86400:
                    target_contests.add(cid)

        if not target_contests: return await channel.send("対象となるコンテストがもうないにゃ！")
        
        user_ac_data = {} 
        for i, user in enumerate(atcoder_ids):
            await status_msg.edit(content=f"**コンテストの決定処理を開始するにゃ！**\n`データ取得中にゃ... ({i+1}/{len(atcoder_ids)}人完了)`")
            user_ac_data[user] = {}
            from_second = 0
            try:
                while True:
                    url = f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={user}&from_second={from_second}"
                    await asyncio.sleep(1.0) 
                    subs = (await asyncio.to_thread(requests.get, url)).json()
                    if not subs:
                        break
                    for sub in subs:
                        if sub["contest_id"] in target_contests:
                            prob_idx = sub["problem_id"].split("_")[-1]
                            if ctype in ["ahc_short", "ahc_long"]:
                                user_ac_data[user].setdefault(sub["contest_id"], {})[prob_idx] = max(
                                sub["point"], user_ac_data[user][sub["contest_id"]].get(prob_idx, 0)
                                )
                            elif sub["result"] == "AC":
                                user_ac_data[user].setdefault(sub["contest_id"], {})[prob_idx] = sub["point"]
                    if len(subs) < 500:
                        break
                    from_second = subs[-1]["epoch_second"] + 1
            except: pass

        await status_msg.edit(content=f" `全員のデータを取得したにゃ！最適な回を計算中にゃ～`")

        valid_contests = [] 
        max_2ac_users = 1 + len(atcoder_ids) // 10

        if ctype in ["ahc_short", "ahc_long"]:
            req_ratio = 0.7
            for cid in target_contests:
                submitted_count = sum(1 for user in atcoder_ids if cid in user_ac_data[user])
                if submitted_count < len(atcoder_ids) * req_ratio:
                    valid_contests.append((cid, 0)) 
        else:
            for cid in target_contests:
                exclude = False
                total_score = 0
                score_4_over = 0
                ac_2_count = 0
                awc_400_over = 0
                awc_3_ac = 0

                if ctype == "abc":
                    is_6_prob = (126 <= int(cid[3:6]) <= 211)
                    for user in atcoder_ids:
                        ac_dict = user_ac_data[user].get(cid, {})
                        ac_set = set(ac_dict.keys())
                        if len(ac_set) >= 5 or any(len(idx)>1 or idx>='f' for idx in ac_set):
                            exclude = True; break
                        score = 0
                        if is_6_prob: score += 1.5 if 'd' in ac_set else 0; score += 4 if 'e' in ac_set else 0
                        else: score += 1 if 'd' in ac_set else 0; score += 3 if 'e' in ac_set else 0
                        total_score += score
                        if score >= 4: score_4_over += 1
                    if not exclude and (score_4_over / len(atcoder_ids)) < 0.35:
                        valid_contests.append((cid, total_score))
                elif ctype == "awc":
                    threshold = len(atcoder_ids) / 10
                    for user in atcoder_ids:
                        ac_dict = user_ac_data[user].get(cid, {})
                        if any(point >= 400 for point in ac_dict.values()):
                            awc_400_over += 1
                        if len(ac_dict) >= 3:
                            awc_3_ac += 1
                        if awc_400_over >= threshold or awc_3_ac >= threshold:
                            exclude = True; break
                    if not exclude:
                        valid_contests.append((cid, total_score))
                else:
                    for user in atcoder_ids:
                        ac_dict = user_ac_data[user].get(cid, {})
                        if len(ac_dict) >= 2:
                            ac_2_count += 1
                        user_score = 0
                        for prob_idx, point in ac_dict.items():
                            if ctype == "arc" and point >= 700: exclude = True; break
                            if ctype == "agc" and point >= 800: exclude = True; break
                            user_score += point / 100.0
                        if exclude: break
                        total_score += user_score
                    
                    if not exclude and ac_2_count <= max_2ac_users:
                        valid_contests.append((cid, total_score))

        if not valid_contests: return await channel.send("ちょうどいい難易度の回が見つからなかったにゃ...")

        if ctype in ["ahc_short", "ahc_long"]:
            chosen_cid = random.choice([c for c, _ in valid_contests])
        else:
            valid_contests.sort(key=lambda x: x[1])
            scores = [(cid, score + 1) for cid, score in valid_contests[:30]]
            min_score_6 = scores[0][1] ** 6
            weights = [min_score_6 / (s ** 6) for _, s in scores]
            chosen_cid = random.choices([c for c, _ in scores], weights=weights, k=1)[0]
        
        history_data.append(chosen_cid)
        await save_data_to_channel(bot)
        await status_msg.delete()

    try:
        contests_data = (await asyncio.to_thread(requests.get, "https://kenkoooo.com/atcoder/resources/contests.json")).json()
        contests_dict = {c["id"]: c for c in contests_data}
    except:
        contests_dict = {}

    duration_sec = 100 * 60
    if chosen_cid in contests_dict:
        duration_sec = contests_dict[chosen_cid].get("duration_second", 100 * 60)

    if str(message_id) in vcons_data:
        vcons_data[str(message_id)]["duration"] = duration_sec
        vcons_data[str(message_id)]["chosen_cid"] = chosen_cid
        await save_data_to_channel(bot)

    await channel.send(
        f"**今回のバチャコンの回が決定したにゃ！！**\n👉 **{chosen_cid.upper()}** (https://atcoder.jp/contests/{chosen_cid})\n"
        f"開始時間は **{start_dt.strftime('%m/%d %H:%M')}** だにゃ！\n"
        f"**ライブ順位表はここにゃ:**  https://virtual-contest-cat.up.railway.app/{chosen_cid.upper()}\n"
    )

    scheduler.add_job(live_standings_loop, 'date', run_date=start_dt, args=[channel_id, message_id, chosen_cid, start_dt, duration_sec])
    end_time = start_dt + datetime.timedelta(seconds=duration_sec + 60)
    scheduler.add_job(aggregate_vcontest, 'date', run_date=end_time, args=[channel_id, message_id, chosen_cid, start_dt, duration_sec])

# ==========================================
# ライブ順位表 & WebSocket処理
# ==========================================
async def live_standings_loop(channel_id, message_id, cid, start_dt, duration_sec=6000):
    import traceback 
    global last_ws_data 
    channel = bot.get_channel(channel_id)
    if channel: await channel.send(f"**{cid.upper()} ライブ順位表が起動したにゃ！**\n👉 URL:  https://virtual-contest-cat.up.railway.app/{cid.upper()}\n")

    start_epoch = int(start_dt.timestamp())
    end_dt = start_dt + datetime.timedelta(seconds=duration_sec)
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'}
    cookies = {'REVEL_SESSION': REVEL_SESSION} if REVEL_SESSION else {}

    is_ahc = cid.startswith("ahc")

    standings = None
    tasks = []
    task_names = {}
    screen_to_assign = {}
    
    for retry in range(5):
        try:
            s_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/contests/{cid}/standings/json", headers=headers, cookies=cookies, timeout=20)
            if s_res.status_code == 200:
                standings = s_res.json()
                for t in standings.get("TaskInfo", []):
                    assignment = t["Assignment"]
                    screen_name = t["TaskScreenName"]
                    tasks.append(assignment)
                    screen_to_assign[screen_name] = assignment
                    task_names[assignment] = f"{assignment} - {t.get('TaskName', 'Problem ' + assignment)}"
                break
            else:
                print(f"[{cid}] 順位表取得エラー: HTTP {s_res.status_code} - {s_res.text[:50]}")
        except Exception as e:
            print(f"[{cid}] 順位表取得例外: {e}")
        await asyncio.sleep(5)
            
    if not standings:
        if channel: 
            await channel.send(f"順位表の初期化に失敗したにゃ...")
        return

    valid_perfs = []
    valid_ranks = []
    valid_p_values = []
    if not is_ahc:
        results = []
        for retry in range(3):
            try:
                r_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/contests/{cid}/results/json", headers=headers, cookies=cookies, timeout=20)
                if r_res.status_code == 200:
                    results = r_res.json()
                    break
            except Exception: pass
            await asyncio.sleep(3)
            
        for r in results:
            p = r.get("Performance", 0)
            is_rated = r.get("IsRated", False)
            rank = r.get("Rank") or r.get("Place")
            if (p > 0 or is_rated) and rank is not None:
                valid_perfs.append((rank, p))
        valid_perfs.sort(key=lambda x: x[0])
        valid_ranks = [x[0] for x in valid_perfs]
        valid_p_values = [x[1] for x in valid_perfs]

    user_ratings = {}
    previous_scores = {} 
    
    # メモリ上で提出履歴と確定済みIDを管理する変数
    #ユーザー個人の提出をのぞかない方式に変更したので必要
    user_subs_history = {}
    processed_sub_ids = set()

    async with aiohttp.ClientSession(headers=headers, cookies=cookies) as session:
        # 初期参加者のレートをあらかじめ取得しておく
        initial_discord_ids = list(vcon_sessions.get(message_id, set()))
        for d_id in initial_discord_ids:
            user = users_data.get(d_id)
            if user and user not in user_ratings:
                try:
                    contest_type_param = "?contestType=heuristic" if is_ahc else ""
                    async with session.get(f"https://atcoder.jp/users/{user}/history/json{contest_type_param}", timeout=10) as r:
                        if r.status == 200:
                            history = await r.json()
                            user_ratings[user] = history[-1].get("NewRating", 0) if history else 0
                except:
                    user_ratings[user] = 0
                await asyncio.sleep(0.7)

        # 監視メインループ
        while datetime.datetime.now(JST) < end_dt:
            loop_start_time = datetime.datetime.now(JST)
            try: 
                discord_ids = list(vcon_sessions.get(message_id, set()))
                interval = 60 if is_ahc and duration_sec > 86400 else 7 #ループ内処理でかかった時間も含めて7s
                
                active_users = set(users_data.get(d_id) for d_id in discord_ids if users_data.get(d_id))
                
                # 途中参加者や新規ユーザーの初期化およびレート取得
                for user in active_users:
                    if user not in user_subs_history:
                        user_subs_history[user] = {}
                    if user not in user_ratings:
                        try:
                            contest_type_param = "?contestType=heuristic" if is_ahc else ""
                            async with session.get(f"https://atcoder.jp/users/{user}/history/json{contest_type_param}", timeout=10) as r:
                                if r.status == 200:
                                    history = await r.json()
                                    user_ratings[user] = history[-1].get("NewRating", 0) if history else 0
                                else:
                                    user_ratings[user] = 0
                        except:
                            user_ratings[user] = 0
                        await asyncio.sleep(0.7)

                # 全体の提出ページ（1〜3ページ目、最新60件）を取得
                pages_html = []
                for page in range(1, 4):
                    url = f"https://atcoder.jp/contests/{cid}/submissions?page={page}"
                    try:
                        async with session.get(url, timeout=10) as r:
                            if r.status == 200:
                                pages_html.append(await r.text())
                    except Exception: pass
                    
                    if page < 3:
                        await asyncio.sleep(1.0) # ページ間で1s休憩

                # HTMLの解析
                new_subs = []
                for html_text in pages_html:
                    soup = BeautifulSoup(html_text, 'lxml')
                    rows = soup.select('table tbody tr')
                    if not rows: continue
                    
                    for row in rows:
                        sub_id = row.get('data-id')
                        if not sub_id or sub_id in processed_sub_ids:
                            continue
                            
                        cells = row.find_all('td')
                        if len(cells) < 8: continue
                        
                        user_link = cells[2].find('a')
                        if not user_link: continue
                        sub_user = user_link.get('href', '').split('/')[-1]
                        
                        if sub_user not in active_users:
                            continue
                            
                        time_tag = cells[0].find('time')
                        if not time_tag: continue
                        sub_epoch = int(datetime.datetime.strptime(time_tag.text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST).timestamp())
                        
                        if not (start_epoch <= sub_epoch <= int(datetime.datetime.now(JST).timestamp())):
                            continue
                            
                        task_link = cells[1].find('a')
                        if task_link:
                            href = task_link.get('href', '')
                            screen_name = href.split('?')[0].strip('/').split('/')[-1]
                            task_idx = screen_to_assign.get(screen_name, screen_name.split('_')[-1].upper())
                        else:
                            task_idx = 'A'

                        score_text = cells[4].text.strip()
                        score = float(score_text) if score_text.replace('.', '', 1).isdigit() else 0
                        result_label = cells[6].find('span')
                        result = result_label.text.strip() if result_label else "WJ"
                        
                        new_subs.append({
                            "user": sub_user, "epoch_second": sub_epoch, 
                            "problem_id": task_idx, "result": result, 
                            "point": score, "id": sub_id
                        })

                # 古い順に辞書へ書き込み（WJの更新・上書き対応）
                for sub in reversed(new_subs):
                    u = sub["user"]
                    s_id = sub["id"]
                    user_subs_history[u][s_id] = sub
                    if sub["result"] not in ["WJ", "WR", "CE"]:
                        processed_sub_ids.add(s_id)

                # ランキングと提出データの作成
                ranking_data = []
                all_subs_data = []
                
                for d_id in discord_ids:
                    user = users_data.get(d_id)
                    if not user: continue
                    sub_rate = user_ratings.get(user, 0)
                    
                    subs = list(user_subs_history.get(user, {}).values())
                    subs.sort(key=lambda x: x["epoch_second"])
                    
                    sub_dan = 1 if sub_rate <= 0 else (0 if sub_rate >= 2800 else (sub_rate % 400) // 100 + 1)
                    
                    for sub in subs:
                        all_subs_data.append({
                            "id": f"{user}_{sub['id']}", "user": user, "user_rate": sub_rate,
                            "dan": sub_dan,
                            "prob": sub["problem_id"], "prob_title": task_names.get(sub["problem_id"], f"Problem {sub['problem_id']}"), 
                            "time": sub["epoch_second"] - start_epoch,
                            "point": sub["point"], "result": sub["result"], "epoch": sub["epoch_second"]
                        })
                    
                    problem_status = {}
                    for sub in subs:
                        task_idx = sub["problem_id"]
                        if task_idx not in problem_status: 
                            problem_status[task_idx] = {'ac_time': -1, 'penalties': 0, 'point': 0, 'temp_penalties': 0}
                        
                        p_data = problem_status[task_idx]
                        elapsed_sec = sub["epoch_second"] - start_epoch
                        point = sub.get("point", 0)
                        
                        if point > p_data['point']:
                            p_data['point'] = point
                            p_data['ac_time'] = elapsed_sec
                            p_data['penalties'] = p_data['temp_penalties']
                            
                        if sub["result"] not in ["CE", "IE", "WJ", "WR"]:
                            p_data['temp_penalties'] += 1

                    total_score = sum(p['point'] for p in problem_status.values())
                    last_ac_time = max([p['ac_time'] for p in problem_status.values() if p['point'] > 0], default=0)
                    total_penalties = sum(p['penalties'] for p in problem_status.values() if p['point'] > 0)
                    
                    if is_ahc: elapsed_penalty_sec = last_ac_time
                    else: elapsed_penalty_sec = last_ac_time + (total_penalties * 300)
                    
                    v_rank = 1
                    current_elapsed_sec = int((datetime.datetime.now(JST) - start_dt).total_seconds())

                    if not is_ahc:
                        for s in standings.get("StandingsData", []):
                            s_score = 0
                            s_last_ac_time = 0
                            s_penalties = 0
                            for task_key, task_res in s.get("TaskResults", {}).items():
                                task_elapsed = task_res.get("Elapsed", 0) / 1000000000
                                task_score = task_res.get("Score", 0) / 100
                                if task_score > 0 and task_elapsed <= current_elapsed_sec:
                                    s_score += task_score
                                    if task_elapsed > s_last_ac_time: s_last_ac_time = task_elapsed
                                    s_penalties += task_res.get("Penalty", 0)
                            s_total_time = s_last_ac_time + (s_penalties * 300)

                            if s_score > total_score: v_rank += 1
                            elif s_score == total_score and total_score > 0 and s_total_time < elapsed_penalty_sec: v_rank += 1
                        
                    perf = "-"
                    new_rate = sub_rate

                    if not is_ahc:
                        if valid_ranks:
                            idx = bisect.bisect_left(valid_ranks, v_rank)
                            if idx == 0: perf = valid_p_values[0]
                            elif idx == len(valid_ranks): perf = valid_p_values[-1]
                            else:
                                diff1 = v_rank - valid_ranks[idx - 1]
                                diff2 = valid_ranks[idx] - v_rank
                                if diff1 <= diff2: perf = valid_p_values[idx - 1]
                                else: perf = valid_p_values[idx]
                        try:
                            if perf != "-" and sub_rate > 0:
                                perf_int = int(perf)
                                x_old = 2.0 ** (sub_rate / 400.0)
                                x_perf = 2.0 ** (perf_int / 400.0)
                                x_new = x_old * 0.9 + x_perf * 0.1
                                new_rate = int(round(400.0 * math.log2(x_new)))
                        except: pass

                    display_name = user 
                    ranking_data.append({
                        "id": user, "display": display_name, "score": int(total_score), "time": elapsed_penalty_sec,
                        "v_rank": v_rank, "perf": perf, "old_rate": sub_rate, "rate": new_rate, 
                        "status": problem_status, "penalties": total_penalties
                    })

                all_subs_data.sort(key=lambda x: x["epoch"])
                ranking_data.sort(key=lambda x: (-x["score"], x["time"]))

                if is_ahc:
                    for idx, rd in enumerate(ranking_data): rd["v_rank"] = idx + 1

                blink_users = []
                for data in ranking_data:
                    user_id = data["id"]
                    score = data["score"]
                    if user_id in previous_scores and previous_scores[user_id] < score: blink_users.append(user_id)
                    previous_scores[user_id] = score
                
                now_dt = datetime.datetime.now(JST)
                elapsed_sec = int((now_dt - start_dt).total_seconds())
                ws_data = {
                    "type": "update", "status": "running", "elapsed": elapsed_sec, "total": duration_sec,
                    "tasks": tasks, "standings": ranking_data, "submissions": all_subs_data, 
                    "blink_users": blink_users 
                }
                
                last_ws_data[cid] = ws_data 
                if len(last_ws_data) > 10:
                    oldest_cid = next(iter(last_ws_data))
                    del last_ws_data[oldest_cid]
                
                if cid in connected_clients:
                    for ws in list(connected_clients[cid]):
                        try: await ws.send_json(ws_data)
                        except Exception: connected_clients[cid].remove(ws)
                
                # 処理にかかった時間を考慮して7秒間隔になるようウェイト調整
                loop_elapsed_time = (datetime.datetime.now(JST) - loop_start_time).total_seconds()
                sleep_time = max(0.0, interval - loop_elapsed_time)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                
            except Exception as e:
                err_msg = traceback.format_exc()
                print(f"内部エラー発生: {err_msg}")
                if channel: await channel.send(f"順位表の更新中にエラーが起きたにゃ！")
                await asyncio.sleep(7)

    final_data = {"type": "update", "status": "finished", "elapsed": duration_sec, "total": duration_sec, "tasks": tasks, "standings": ranking_data, "submissions": all_subs_data}
    last_ws_data[cid] = final_data 
    if len(last_ws_data) > 10:
        oldest_cid = next(iter(last_ws_data))
        del last_ws_data[oldest_cid]

    if cid in connected_clients:
        for ws in list(connected_clients[cid]):
            try: await ws.send_json(final_data)
            except: pass

# ==========================================
#  最終結果：自動集計・パフォ＆レート計算
# ==========================================
async def aggregate_vcontest(channel_id, message_id, cid, start_dt, duration_sec=6000):
    channel = bot.get_channel(channel_id)
    if not channel: return
    await channel.send(f" **{cid.upper()} バチャコン終了にゃ！！**\n`結果とパフォーマンスを持ってくるにゃ...`")

    discord_ids = list(vcon_sessions.get(message_id, set()))
    
    start_epoch = int(start_dt.timestamp())
    end_epoch = start_epoch + duration_sec
    is_ahc = cid.startswith("ahc")

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'}
    cookies = {'REVEL_SESSION': REVEL_SESSION} if REVEL_SESSION else {}

    standings = None
    tasks = []
    screen_to_assign = {}
    
    for retry in range(5):
        try:
            s_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/contests/{cid}/standings/json", headers=headers, cookies=cookies, timeout=20)
            if s_res.status_code == 200:
                standings = s_res.json()
                for t in standings.get("TaskInfo", []):
                    tasks.append(t["Assignment"])
                    screen_to_assign[t["TaskScreenName"]] = t["Assignment"]
                break
            else: print(f"[{cid}] 終了集計_順位表取得エラー: HTTP {s_res.status_code}")
        except Exception: pass
        await asyncio.sleep(5)
        
    if not standings: return await channel.send(f"本番データの取得に失敗してパフォが計算できないにゃ...")

    valid_perfs = []
    valid_ranks = []
    valid_p_values = []
    if not is_ahc:
        results = []
        for retry in range(3):
            try:
                r_res = await asyncio.to_thread(requests.get, f"https://atcoder.jp/contests/{cid}/results/json", headers=headers, cookies=cookies, timeout=20)
                if r_res.status_code == 200:
                    results = r_res.json()
                    break
            except Exception: pass
            await asyncio.sleep(3)
            
        for r in results:
            p = r.get("Performance", 0)
            is_rated = r.get("IsRated", False)
            rank = r.get("Rank") or r.get("Place")
            if (p > 0 or is_rated) and rank is not None:
                valid_perfs.append((rank, p))
        valid_perfs.sort(key=lambda x: x[0])
        valid_ranks = [x[0] for x in valid_perfs]
        valid_p_values = [x[1] for x in valid_perfs]
    
    ranking_data = []

    async with aiohttp.ClientSession(headers=headers, cookies=cookies) as session:
        async def fetch_final_user_data(user):
            current_rating = 0
            try:
                contest_type_param = "?contestType=heuristic" if is_ahc else ""
                async with session.get(f"https://atcoder.jp/users/{user}/history/json{contest_type_param}", timeout=10) as r:
                    if r.status == 200:
                        history = await r.json()
                        current_rating = history[-1].get("NewRating", 0) if history else 0
            except: pass

            pages_html = []
            for page in range(1, 4):
                try:
                    url = f"https://atcoder.jp/contests/{cid}/submissions?page={page}&f.User={user}"
                    async with session.get(url, timeout=10) as r:
                        if r.status == 200:
                            pages_html.append(await r.text())
                        else: break
                except: break
            return user, current_rating, pages_html

        tasks_req = [fetch_final_user_data(users_data[d_id]) for d_id in discord_ids if users_data.get(d_id)]
        final_results = await asyncio.gather(*tasks_req)

        for user, current_rating, pages_html in final_results:
            subs = []
            for html_text in pages_html:
                soup = BeautifulSoup(html_text, 'lxml')
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
                        if task_link:
                            href = task_link.get('href', '')
                            screen_name = href.split('?')[0].strip('/').split('/')[-1]
                            task_idx = screen_to_assign.get(screen_name, screen_name.split('_')[-1].upper())
                        else:
                            task_idx = 'A'
                            
                        score_text = cells[4].text.strip()
                        score = float(score_text) if score_text.replace('.', '', 1).isdigit() else 0
                        result_label = cells[6].find('span')
                        result = result_label.text.strip() if result_label else "WJ"

                        subs.append({"epoch_second": sub_epoch, "problem_id": task_idx, "result": result, "point": score})
                if len(rows) < 20: break 

            subs.sort(key=lambda x: x["epoch_second"])
            
            problem_status = {}
            for sub in subs:
                task_idx = sub["problem_id"]
                if task_idx not in problem_status: 
                    problem_status[task_idx] = {'ac_time': -1, 'penalties': 0, 'point': 0, 'temp_penalties': 0}
                
                p_data = problem_status[task_idx]
                elapsed_sec = sub["epoch_second"] - start_epoch
                point = sub.get("point", 0)
                
                if point > p_data['point']:
                    p_data['point'] = point
                    p_data['ac_time'] = elapsed_sec
                    p_data['penalties'] = p_data['temp_penalties']
                
                if sub["result"] not in ["CE", "IE", "WJ", "WR"]:
                    p_data['temp_penalties'] += 1

            total_score = sum(p['point'] for p in problem_status.values())
            last_ac_time = max([p['ac_time'] for p in problem_status.values() if p['point'] > 0], default=0)
            total_penalties = sum(p['penalties'] for p in problem_status.values() if p['point'] > 0)
            
            if is_ahc: elapsed_penalty_sec = last_ac_time
            else: elapsed_penalty_sec = last_ac_time + (total_penalties * 300)
            
            v_rank = 1
            if not is_ahc:
                for s in standings["StandingsData"]:
                    s_score = s["TotalResult"]["Score"] / 100
                    s_elapsed = s["TotalResult"]["Elapsed"] / 1000000000
                    if s_score > total_score: v_rank += 1
                    elif s_score == total_score and s_elapsed < elapsed_penalty_sec: v_rank += 1
                
            perf = "-"
            if not is_ahc:
                if valid_ranks:
                    idx = bisect.bisect_left(valid_ranks, v_rank)
                    if idx == 0: perf = valid_p_values[0]
                    elif idx == len(valid_ranks): perf = valid_p_values[-1]
                    else:
                        diff1 = v_rank - valid_ranks[idx - 1]
                        diff2 = valid_ranks[idx] - v_rank
                        if diff1 <= diff2: perf = valid_p_values[idx - 1]
                        else: perf = valid_p_values[idx]

            display_name = user
            ranking_data.append({
                "user": user, "display": display_name,
                "score": int(total_score), "time": elapsed_penalty_sec,
                "rank": v_rank, "perf": perf,
                "current_rating": current_rating,
                "status": problem_status, "penalties": total_penalties
            })

    ranking_data.sort(key=lambda x: (-x["score"], x["time"]))

    if is_ahc:
        for idx, rd in enumerate(ranking_data): rd["rank"] = idx + 1

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
            p_data = data["status"].get(t, {'ac_time': -1, 'penalties': 0, 'point': 0})
            pens = p_data["penalties"]
            if p_data["ac_time"] != -1:
                cross = "" if pens == 0 else ("❌" * pens if pens < 3 else f"❌x{pens}")
                tm, ts = divmod(p_data["ac_time"], 60)
                task_strs.append(f"{t}: {cross}{int(p_data['point'])}pts({int(tm)}:{int(ts):02d})")
            else:
                cross = "-" if pens == 0 else ("❌" * pens if pens < 3 else f"❌x{pens}")
                task_strs.append(f"{t}: {cross}")

        task_line = " | ".join(task_strs) if task_strs else "提出なし"
        
        perf = data["perf"]
        current_rating = data["current_rating"]
        rating_str = ""
        try:
            if not is_ahc and current_rating > 0:
                perf_int = int(perf)
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
    
    if str(message_id) in vcons_data:
        del vcons_data[str(message_id)]
        await save_data_to_channel(bot)

# ==========================================
#  Web & WebSocket サーバー 
# ==========================================
async def handle_root(request):
    html = """
    <html><body style="background:#1e1e1e; color:#fff; text-align:center; padding:50px; font-family:sans-serif;">
        <h2>コンテストIDが指定されていないにゃ！</h2>
        <p>DiscordのBotが投稿したリンクからアクセスしてにゃ</p>
    </body></html>
    """
    return web.Response(text=html, content_type='text/html')

async def handle_index(request):
    contest_id = request.match_info.get('contest_id', '').lower()
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            html = f.read()
            
            # 【修正箇所】localhost 以外（Railway等）はすべて wss:// を使用するように変更
            is_secure = "localhost" not in request.host
            ws_protocol = "wss://" if is_secure else "ws://"
            ws_url = f"{ws_protocol}{request.host}/{contest_id}/ws"
            
            html = html.replace("/* WEBSOCKET_INJECTION_POINT */", f"var WS_URL = '{ws_url}';")
        return web.Response(text=html, content_type='text/html')
    except Exception as e:
        return web.Response(text=f"index.html が見つからないにゃ... ({e})", status=404)

async def websocket_handler(request):
    contest_id = request.match_info.get('contest_id', '').lower()
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    if contest_id not in connected_clients:
        connected_clients[contest_id] = set()
    connected_clients[contest_id].add(ws)
    print(f"Web画面が繋がったにゃ！ ({contest_id.upper()})")
    
    if contest_id in last_ws_data and last_ws_data[contest_id]:
        try:
            await ws.send_json(last_ws_data[contest_id])
        except Exception: pass

    try:
        async for msg in ws: pass
    finally:
        if ws in connected_clients.get(contest_id, set()):
            connected_clients[contest_id].remove(ws)
        print(f"Web画面が閉じたにゃ ({contest_id.upper()})")
    return ws

async def web_server_runner():
    app = web.Application()
    app.add_routes([
        web.get('/', handle_root),
        web.get('/{contest_id}', handle_index),
        web.get('/{contest_id}/ws', websocket_handler)
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"Webサーバーをポート {PORT} で起動したにゃ！")

async def main():
    async with bot:
        bot.loop.create_task(web_server_runner())
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
