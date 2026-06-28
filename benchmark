import time
import math
import os
from aiohttp import web

def run_benchmark():
    log = []
    log.append("🚀 Render CPUベンチマークを開始します...\n")
    
    # ----------------------------------------
    # テスト1: 整数計算（素数判定）
    # ----------------------------------------
    log.append("[テスト1] 整数計算 (素数判定 1〜50,000)")
    start_time = time.time()
    primes = []
    for num in range(2, 50000):
        is_prime = True
        for i in range(2, int(math.sqrt(num)) + 1):
            if num % i == 0:
                is_prime = False
                break
        if is_prime:
            primes.append(num)
    calc_time = time.time() - start_time
    log.append(f"結果: {calc_time:.3f} 秒 (発見数: {len(primes)})\n")

    # ----------------------------------------
    # テスト2: 文字列処理（ダミーHTMLの大量パース処理の模倣）
    # ----------------------------------------
    log.append("[テスト2] 文字列処理・リスト操作 (1,000万文字の置換と分割)")
    start_time = time.time()
    dummy_html = "<tr><td>100</td><td>AC</td><td>10:00</td></tr>" * 200000
    dummy_html = dummy_html.replace("td>", "div>")
    rows = dummy_html.split("<tr>")
    str_time = time.time() - start_time
    log.append(f"結果: {str_time:.3f} 秒 (行数: {len(rows)})\n")

    # ----------------------------------------
    # 総合評価
    # ----------------------------------------
    total_time = calc_time + str_time
    log.append("========================================")
    log.append(f"⏱️ 総合実行時間: {total_time:.3f} 秒")
    
    if total_time < 0.5:
        log.append("💻 判定: 【超優秀】 ローカルPC（つよつよ）レベルです")
    elif total_time < 2.0:
        log.append("💻 判定: 【普通】 通常のVPSや有料サーバーレベルです")
    elif total_time < 5.0:
        log.append("💻 判定: 【やや遅い】 無料サーバーの標準的な速度です")
    else:
        log.append("💻 判定: 【激遅】 CPU制限(スロットリング)を完全に受けています！")
    log.append("========================================")
    
    return "\n".join(log)

async def handle_benchmark(request):
    # ブラウザからアクセスが来た時にベンチマークを実行
    result_text = run_benchmark()
    print(result_text) # 念のためログにも出す
    return web.Response(text=result_text, content_type='text/plain', charset='utf-8')

app = web.Application()
app.add_routes([web.get('/', handle_benchmark)])

if __name__ == '__main__':
    # Renderが指定するPORT（デフォルトは10000等）でサーバーを立てる
    port = int(os.environ.get('PORT', 8080))
    print(f"ベンチマークサーバーをポート {port} で起動しました。")
    print("WebブラウザでURLにアクセスして結果を確認してください。")
    web.run_app(app, host='0.0.0.0', port=port)
