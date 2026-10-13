# Contains functions to call APIs or otherwise gather data

from pycoingecko import CoinGeckoAPI
cg = CoinGeckoAPI()

####### CRYPTO CURRENT PRICE DATA ##########
def get_bitcoin():
    price = cg.get_price(ids='bitcoin', vs_currencies='usd')
    return str(price['bitcoin']['usd']) + ".00"

def get_ethereum():
    price = cg.get_price(ids='ethereum', vs_currencies='usd')
    return str(price['ethereum']['usd'])

def get_solana():
    price = cg.get_price(ids='solana', vs_currencies='usd')
    return str(price['solana']['usd'])

def get_ripple():
    price = cg.get_price(ids='ripple', vs_currencies='usd')
    return str(price['ripple']['usd'])
