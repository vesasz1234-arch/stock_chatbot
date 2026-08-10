from real_trade_alpha_bot import RealTradeAlphaBot
import datetime

class VerifyBot(RealTradeAlphaBot):
    def send_real_buy_order(self, name, ticker, curr_price):
        print(f"[TEST MODE] Signal Detected! {name}({ticker}) @ {curr_price} - WOULD BUY NOW")
        return True, 1

    def send_real_sell_order(self, name, ticker, qty, reason):
        print(f"[TEST MODE] Would Sell {name} due to {reason}")
        return True

    def send_discord_msg(self, message):
        print(f"[TEST DISCORD] {message}")

if __name__ == "__main__":
    bot = VerifyBot(top_n=200)
    print("🚀 [VERIFICATION MODE] 테스트 가동 시작...")
    bot.update_universe()
    for name, ticker in list(bot.universe.items())[:10]:
        print(f"Scanning {name}...")
        bot.scan_and_trade(name, ticker)
    print("✨ 테스트 완료.")

