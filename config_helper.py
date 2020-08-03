import os
import pickle 

os.system('mkdir data')

email_pw = {'pw': <<email pw>>}
spotify_credentials = {'SPOTIPY_CLIENT_ID': <<client_id>>,
 'SPOTIPY_CLIENT_SECRET': <<client secret>>,
 'SPOTIPY_REDIRECT_URI': <<redirect_uri>>}

with open('data/spotify_credentials.pkl', 'wb') as hnd:
    pickle.dump(spotify_credentials, hnd)

with open('data/email_pw.pkl', 'wb') as hnd:
    pickle.dump(email_pw, hnd)

