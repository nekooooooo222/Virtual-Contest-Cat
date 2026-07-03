import requests
import json

# ==========================================
# 🔍 ここに確認したい REVEL_SESSION を貼ってください
# ==========================================
REVEL_SESSION = "13fd8792a53e6cb92111495b417914e49ea167f0-%00csrf_token%3ACOPnYgnKwPuoQgjo3ey%2F7DPrER2z3iX4%2FHb55VjEXO0%3D%00%00_TS%3A1798646757%00%00SessionKey%3Ad5d267e8567c5be2892c0e975f02b806233bb1281bf88c82e213bf4d7b6e2fe1%00%00UserScreenName%3Anekooooooo%00%00UserName%3Anekooooooo%00%00a%3Afalse%00%00w%3Afalse%00"

def test_atcoder_connection():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    cookies = {'REVEL_SESSION': REVEL_SESSION} if REVEL_SESSION else {}

    print("① ログイン状態の確認（/settings にアクセスします）...")
    try:
        # allow_redirects=False にすることで、未ログイン時の /login への強制転送を検知します
        res_settings = requests.get("https://atcoder.jp/settings", headers=headers, cookies=cookies, timeout=10, allow_redirects=False)
        
        if res_settings.status_code == 403:
            print("❌ 【結果: IPブロック】 HTTP 403 拒否")
            print("   Cloudflareなどのセキュリティに弾かれています。")
            print("   セッションが合っているか以前に、この通信環境（IPアドレス）がアクセス禁止状態です。\n")
        elif res_settings.status_code in (301, 302):
            redirect_url = res_settings.headers.get('Location', '')
            if '/login' in redirect_url:
                print("❌ 【結果: セッション無効】 未ログイン状態です")
                print("   /login に転送されました。REVEL_SESSION が間違っているか、期限切れです。\n")
            else:
                print(f"⚠️ 謎の転送先: {redirect_url}\n")
        elif res_settings.status_code == 200:
            print("✅ 【結果: ログイン成功！】")
            print("   セッションは完全に有効です。\n")
        else:
            print(f"⚠️ 未知のステータスコード: {res_settings.status_code}\n")
            
    except Exception as e:
        print(f"通信エラー: {e}\n")
        return

    print("② 順位表APIの取得テスト（ABC292）...")
    try:
        res_json = requests.get("https://atcoder.jp/contests/abc292/standings/json", headers=headers, cookies=cookies, timeout=10)
        
        if res_json.status_code == 200:
            try:
                data = res_json.json()
                print("✅ 【結果: JSON取得成功！】")
                print(f"   データ取得完了。参加者数: {len(data.get('StandingsData', []))} 人")
            except json.JSONDecodeError:
                print("❌ 【結果: JSON解析失敗】")
                print("   ステータス200ですが、中身がJSONではありません。")
                print("   (おそらくログイン画面のHTMLが返ってきています。REVEL_SESSIONが無効です)")
        elif res_json.status_code == 403:
            print("❌ 【結果: APIブロック】 HTTP 403 拒否")
            print("   APIへのアクセスがセキュリティで弾かれました。")
            print("   エラー内容の先頭:", res_json.text[:100])
        else:
            print(f"⚠️ API取得エラー: HTTP {res_json.status_code}")
            
    except Exception as e:
        print(f"通信エラー: {e}")

if __name__ == "__main__":
    test_atcoder_connection()
