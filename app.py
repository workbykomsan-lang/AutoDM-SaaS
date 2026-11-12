import os
import random
import requests
from flask import Flask, request, redirect, jsonify, render_template_string
from supabase import create_client, Client

app = Flask(__name__)

# إعدادات Supabase الخاصة بك
SUPABASE_URL = "https://dotzukhrajaezbqiuzoj.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# إعدادات Meta App
META_APP_ID = os.environ.get("META_APP_ID")
META_APP_SECRET = os.environ.get("META_APP_SECRET")
VERIFY_TOKEN = "KOMSAN_SAAS_2026"
GRAPH_API = "https://graph.facebook.com/v21.0"

@app.route('/')
def home():
    return "AutoDM SaaS Engine is Running 🚀"

# 1. التحقق من Webhook ميتا
@app.route('/webhook', methods=['GET'])
def verify():
    if request.args.get('hub.verify_token') == VERIFY_TOKEN:
        return request.args.get('hub.challenge'), 200
    return 'Forbidden', 403

# 2. استقبال التعليقات والرد عليها
@app.route('/webhook', methods=['POST'])
def webhook_handler():
    data = request.get_json()
    if data.get('object') != 'instagram':
        return jsonify({"status": "ignored"}), 200

    for entry in data.get('entry', []):
        ig_business_id = entry.get('id')
        for change in entry.get('changes', []):
            if change.get('field') == 'comments':
                val = change.get('value', {})
                comment_id = val.get('id')
                comment_text = val.get('text', '').strip().lower()
                media_id = val.get('media', {}).get('id')
                sender_id = val.get('from', {}).get('id')

                if sender_id == ig_business_id:
                    continue

                acc_query = supabase.table("instagram_accounts").select("*, users(*)").eq("ig_business_id", ig_business_id).execute()
                if not acc_query.data:
                    continue

                account = acc_query.data[0]
                user = account['users']
                token = account['page_access_token']

                if user['subscription_status'] != 'active' or user['messages_used'] >= user['messages_limit']:
                    continue

                rules_query = supabase.table("automation_rules").select("*").eq("ig_account_id", account['id']).eq("media_id", media_id).eq("is_active", True).execute()
                
                for rule in rules_query.data:
                    kw = rule['keyword'].strip().lower()
                    if kw == "*" or kw in comment_text:
                        public_reply = random.choice(rule['public_replies'])
                        requests.post(f"{GRAPH_API}/{comment_id}/replies", data={
                            "message": public_reply,
                            "access_token": token
                        })

                        message_payload = {"text": rule['dm_message']}
                        if rule.get('dm_button_text') and rule.get('dm_button_url'):
                            message_payload = {
                                "attachment": {
                                    "type": "template",
                                    "payload": {
                                        "template_type": "button",
                                        "text": rule['dm_message'],
                                        "buttons": [{
                                            "type": "web_url",
                                            "url": rule['dm_button_url'],
                                            "title": rule['dm_button_text']
                                        }]
                                    }
                                }
                            }

                        dm_res = requests.post(f"{GRAPH_API}/{ig_business_id}/messages", json={
                            "recipient": {"comment_id": comment_id},
                            "message": message_payload,
                            "access_token": token
                        })

                        if dm_res.status_code == 200:
                            supabase.table("users").update({"messages_used": user['messages_used'] + 1}).eq("id", user['id']).execute()
                            supabase.table("automation_rules").update({"triggered_count": rule['triggered_count'] + 1}).eq("id", rule['id']).execute()
                        break

    return jsonify({"status": "ok"}), 200

# 3. بدء ربط حساب الإنستجرام (OAuth)
@app.route('/connect-instagram/<user_id>')
def connect_instagram(user_id):
    redirect_uri = request.host_url.replace("http://", "https://") + "auth/facebook/callback"
    permissions = "instagram_basic,instagram_manage_comments,instagram_manage_messages,pages_show_list,pages_read_engagement"
    auth_url = (
        f"https://www.facebook.com/v21.0/dialog/oauth?"
        f"client_id={META_APP_ID}&redirect_uri={redirect_uri}&"
        f"scope={permissions}&state={user_id}"
    )
    return redirect(auth_url)

# 4. استقبال التوكن وحفظ الحساب
@app.route('/auth/facebook/callback')
def facebook_callback():
    code = request.args.get('code')
    user_id = request.args.get('state')
    redirect_uri = request.host_url.replace("http://", "https://") + "auth/facebook/callback"

    token_res = requests.get(f"{GRAPH_API}/oauth/access_token", params={
        "client_id": META_APP_ID,
        "redirect_uri": redirect_uri,
        "client_secret": META_APP_SECRET,
        "code": code
    }).json()
    short_token = token_res.get("access_token")

    long_token_res = requests.get(f"{GRAPH_API}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "fb_exchange_token": short_token
    }).json()
    long_user_token = long_token_res.get("access_token")

    pages_res = requests.get(f"{GRAPH_API}/me/accounts", params={
        "fields": "id,name,access_token,instagram_business_account{id,username}",
        "access_token": long_user_token
    }).json()

    for page in pages_res.get("data", []):
        ig_account = page.get("instagram_business_account")
        if ig_account:
            ig_id = ig_account["id"]
            ig_username = ig_account.get("username", "")
            page_token = page["access_token"]

            requests.post(f"{GRAPH_API}/{page['id']}/subscribed_apps", params={
                "subscribed_fields": "feed",
                "access_token": page_token
            })

            supabase.table("instagram_accounts").upsert({
                "user_id": user_id,
                "ig_business_id": ig_id,
                "ig_username": ig_username,
                "page_access_token": page_token,
                "is_active": True
            }, on_conflict="ig_business_id").execute()

    return redirect("/dashboard?connected=true")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
