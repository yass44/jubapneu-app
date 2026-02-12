import streamlit as st
import pandas as pd
import plotly.express as px
from supabase import create_client, Client
from datetime import datetime
import io
import re
import pdfplumber
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import time

# --- CONFIGURATION PAGE ---
st.set_page_config(page_title="JubaPneu ERP", layout="wide", page_icon="🛞")

# --- STYLE CSS ---
st.markdown("""
<style>
    .stSelectbox > label { font-size:120%; font-weight:bold; color:#FF4B4B; }
    .stButton > button { width: 100%; }
    div[data-testid="stMetricValue"] { font-size: 1.8rem; }
</style>
""", unsafe_allow_html=True)

# =========================================================
# 🔒 SYSTÈME D'AUTHENTIFICATION (LE PORTIER)
# =========================================================
def check_password():
    """Renvoie True si l'utilisateur a le bon mot de passe."""

    def password_entered():
        """Vérifie si le mot de passe saisi est correct."""
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # On ne garde pas le mot de passe en mémoire
        else:
            st.session_state["password_correct"] = False

    # Si déjà validé dans la session, on laisse passer
    if st.session_state.get("password_correct", False):
        return True

    # Sinon, on affiche le champ mot de passe
    st.title("🔒 Accès Sécurisé JubaPneu")
    st.text_input(
        "Veuillez entrer le mot de passe administrateur :", 
        type="password", 
        on_change=password_entered, 
        key="password"
    )
    
    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("❌ Mot de passe incorrect.")

    return False

# Si le mot de passe n'est pas bon, on arrête TOUT ici.
if not check_password():
    st.stop()

# =========================================================
# ✅ APPLICATION PRINCIPALE (Se charge seulement si connecté)
# =========================================================

# --- CONNEXION SUPABASE ---
@st.cache_resource
def init_connection():
    try:
        # On essaie de lire depuis les secrets (Cloud ou Local)
        # Note : Pour le local, assure-toi d'avoir un fichier .streamlit/secrets.toml OU secrets_config.py
        # Pour simplifier ici, on suppose que secrets est géré par Streamlit Cloud ou un fichier local simulé
        
        # Tentative import local pour compatibilité PC
        try:
            import secrets_config
            local_url = secrets_config.SUPABASE_URL
            local_key = secrets_config.SUPABASE_KEY
        except ImportError:
            local_url = None
            local_key = None

        if "SUPABASE_URL" in st.secrets:
            url = st.secrets["SUPABASE_URL"]
            key = st.secrets["SUPABASE_KEY"]
        elif local_url:
            url = local_url
            key = local_key
        else:
            st.error("Clés Supabase introuvables.")
            return None
            
        return create_client(url, key)
    except Exception as e:
        st.error(f"Erreur connexion : {e}")
        return None

supabase = init_connection()

# --- FONCTIONS DATA ---
def load_all_data():
    if not supabase: return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    
    stock = pd.DataFrame(supabase.table('articles').select("*").execute().data)
    mouv = pd.DataFrame(supabase.table('mouvements_stock').select("*").execute().data)
    clients = pd.DataFrame(supabase.table('clients').select("*").order('nom').execute().data)
    factures = pd.DataFrame(supabase.table('factures_entete').select('*, clients(*)').order('created_at', desc=True).execute().data)
    services = pd.DataFrame(supabase.table('services').select("*").order('description').execute().data)
    
    return stock, mouv, clients, factures, services

def get_facture_lines(facture_id):
    res = supabase.table('factures_lignes').select('*, articles(*)').eq('facture_id', facture_id).execute()
    return res.data

# --- GÉNÉRATEUR PDF ---
def generer_pdf(facture_id, client_dict, lignes, total_ttc, numero_facture, date_str=None):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    if not date_str: date_str = datetime.now().strftime('%d/%m/%Y')
    
    c.setFont("Helvetica-Bold", 16); c.drawString(50, height-50, "JUBAPNEU")
    c.setFont("Helvetica", 10)
    c.drawString(50, height-70, "123 Route du Garage, 57000 METZ")
    c.drawString(50, height-85, "SIRET: 123 456 789 00012")
    
    c.setFont("Helvetica-Bold", 14); c.drawRightString(width-50, height-50, "FACTURE")
    c.setFont("Helvetica", 12); c.drawRightString(width-50, height-70, f"N° {numero_facture}")
    c.drawString(width-150, height-90, f"Date : {date_str}")
    
    c.rect(300, height-200, 250, 80)
    c.setFont("Helvetica-Bold", 12); c.drawString(310, height-135, f"Client : {client_dict.get('nom', 'Inconnu')}")
    c.setFont("Helvetica", 10)
    if client_dict.get('adresse'): c.drawString(310, height-150, f"{client_dict['adresse']} {client_dict.get('ville','')}")
    if client_dict.get('siret'): c.drawString(310, height-185, f"SIRET: {client_dict['siret']}")
    
    y = height - 250
    c.line(50, y, width-50, y)
    c.drawString(50, y+5, "Description"); c.drawString(350, y+5, "Qté"); c.drawString(400, y+5, "P.U."); c.drawString(500, y+5, "Total")
    y -= 20
    for l in lignes:
        desc = l.get('desc') or (f"Pneu {l['articles']['marque']} {l['articles']['dimension_complete']}" if l.get('articles') else "Service")
        qte = l.get('qte') or l.get('quantite')
        prix = l.get('prix') or l.get('prix_vente_unitaire')
        c.drawString(50, y, str(desc)[:60])
        c.drawString(350, y, str(qte))
        c.drawString(400, y, f"{prix:.2f}")
        c.drawString(500, y, f"{qte*prix:.2f}")
        y -= 20
    
    c.line(50, y, width-50, y)
    c.setFont("Helvetica-Bold", 12); c.drawRightString(width-50, y-30, f"TOTAL : {total_ttc:.2f} €")
    c.showPage(); c.save(); buffer.seek(0)
    return buffer

# --- ANALYSE PDF DELDO ---
def analyser_ligne_deldo(description):
    infos = {"valid": False, "dimension_complete": description, "largeur": None, "hauteur": None, "diametre": None, "charge": "", "vitesse": "", "saison": "Été", "marque": "Inconnue"}
    regex_deldo = r"(\d{3})\s+(\d{2})\s+[A-Z]+\s+(\d{2})\s+(\d{2,3})\s+([A-Z])\s"
    match = re.search(regex_deldo, description)
    if match:
        infos["valid"] = True; infos["largeur"] = int(match.group(1)); infos["hauteur"] = int(match.group(2)); infos["diametre"] = int(match.group(3))
        infos["charge"] = match.group(4); infos["vitesse"] = match.group(5)
        infos["dimension_complete"] = f"{infos['largeur']}/{infos['hauteur']} R{infos['diametre']} {infos['charge']}{infos['vitesse']}"
    else: return infos
    desc_upper = description.upper()
    if "AS" in desc_upper or "4S" in desc_upper or "ALL" in desc_upper: infos["saison"] = "4 Saisons"
    elif "WINTER" in desc_upper or "HIVER" in desc_upper: infos["saison"] = "Hiver"
    mots = description.split(); 
    if mots: infos["marque"] = mots[0]
    return infos

# --- INITIALISATION ---
df_stock, df_hist, df_clients, df_factures, df_services = load_all_data()
if 'panier' not in st.session_state: st.session_state.panier = []
if 'facture_reussie' not in st.session_state: st.session_state.facture_reussie = None

# =========================================================
# 🗄️ NAVIGATION
# =========================================================
st.sidebar.title("🗄️ JubaPneu")
tiroir = st.sidebar.selectbox("Module :", ["📦 STOCK", "💰 FACTURATION", "📊 STATISTIQUES"])
page = ""
st.sidebar.markdown("---")

if tiroir == "📦 STOCK":
    page = st.sidebar.radio("Nav", ["Stock Actuel", "📥 Importer Facture Fournisseur", "Historique Mouvements"])
elif tiroir == "💰 FACTURATION":
    page = st.sidebar.radio("Nav", ["Nouvelle Facture", "Mes Factures", "Clients", "Gestion Services"])
elif tiroir == "📊 STATISTIQUES":
    page = st.sidebar.radio("Nav", ["Chiffre d'Affaires", "Top Ventes", "Valeur Stock"])

# =========================================================
# MODULE : STOCK
# =========================================================
if page == "Stock Actuel":
    st.title("📦 Stock Disponible")
    if not df_stock.empty:
        df_view = df_stock[df_stock['stock_actuel'] > 0].copy()
        c1, c2 = st.columns([2,1])
        search = c1.text_input("🔍 Rechercher", placeholder="205/55, Michelin...")
        saisons = c2.multiselect("Saison", df_view['saison'].unique())
        if search: df_view = df_view[df_view.astype(str).apply(lambda x: x.str.contains(search, case=False)).any(axis=1)]
        if saisons: df_view = df_view[df_view['saison'].isin(saisons)]
        st.dataframe(df_view[['dimension_complete', 'stock_actuel', 'marque', 'saison', 'charge', 'vitesse', 'pmp_achat']], column_config={"stock_actuel": st.column_config.NumberColumn("En Stock", format="%d 🛞"), "pmp_achat": st.column_config.NumberColumn("PMP Achat", format="%.2f €")}, use_container_width=True, height=600)
    else: st.info("Stock vide.")

elif page == "📥 Importer Facture Fournisseur":
    st.title("📥 Importer une Facture Deldo")
    st.info("Ce module remplace le robot. Glissez le PDF pour mettre à jour le stock automatiquement.")
    uploaded_file = st.file_uploader("Choisir un fichier PDF", type="pdf")
    if uploaded_file is not None:
        st.write("🔎 Analyse en cours...")
        articles_trouves = []
        with pdfplumber.open(uploaded_file) as pdf:
            for page_pdf in pdf.pages:
                text = page_pdf.extract_text()
                if not text: continue
                for ligne in text.split('\n'):
                    match_ligne = re.search(r"^(\d+)\s+(.+)\s+(\d+\.\d{2})\s+(\d+\.\d{2})$", ligne.strip())
                    if match_ligne:
                        qte = int(match_ligne.group(1)); desc_brute = match_ligne.group(2); prix = float(match_ligne.group(3))
                        infos = analyser_ligne_deldo(desc_brute)
                        if infos["valid"]: articles_trouves.append({"Description": infos['dimension_complete'], "Marque": infos['marque'], "Qté": qte, "Prix Achat": prix, "Saison": infos['saison'], "_infos": infos})

        if articles_trouves:
            st.success(f"✅ {len(articles_trouves)} lignes de pneus détectées !")
            st.dataframe(pd.DataFrame(articles_trouves)[['Description', 'Marque', 'Qté', 'Prix Achat']], use_container_width=True)
            if st.button("🚀 CONFIRMER L'IMPORTATION ET METTRE À JOUR LE STOCK", type="primary"):
                progress_bar = st.progress(0)
                for i, item in enumerate(articles_trouves):
                    infos = item['_infos']; qte = item['Qté']; prix = item['Prix Achat']
                    res = supabase.table('articles').select("*").eq('dimension_complete', infos['dimension_complete']).eq('marque', infos['marque']).execute()
                    art_id = None
                    if res.data:
                        exist = res.data[0]; art_id = exist['id']; old_stock = exist['stock_actuel']; old_pmp = float(exist['pmp_achat'] or 0)
                        new_stock = old_stock + qte; new_pmp = ((old_stock * old_pmp) + (qte * prix)) / new_stock if new_stock > 0 else prix
                        supabase.table('articles').update({'stock_actuel': new_stock, 'pmp_achat': new_pmp}).eq('id', art_id).execute()
                    else:
                        new_art = {'dimension_complete': infos['dimension_complete'], 'largeur': infos['largeur'], 'hauteur': infos['hauteur'], 'diametre': infos['diametre'], 'charge': infos['charge'], 'vitesse': infos['vitesse'], 'marque': infos['marque'], 'saison': infos['saison'], 'stock_actuel': qte, 'pmp_achat': prix}
                        res_ins = supabase.table('articles').insert(new_art).execute(); art_id = res_ins.data[0]['id']
                    supabase.table('mouvements_stock').insert({'article_id': art_id, 'type_mouvement': 'ACHAT', 'quantite': qte, 'prix_achat_unitaire': prix, 'lien_facture_fournisseur': uploaded_file.name, 'created_at': datetime.now().strftime("%Y-%m-%d")}).execute()
                    progress_bar.progress((i + 1) / len(articles_trouves))
                st.success("🎉 Importation terminée !"); st.balloons(); st.cache_resource.clear()
        else: st.warning("⚠️ Aucune ligne valide trouvée.")

elif page == "Historique Mouvements":
    st.title("📜 Historique Mouvements")
    if not df_hist.empty: df_hist['created_at'] = pd.to_datetime(df_hist['created_at']); st.dataframe(df_hist.sort_values('created_at', ascending=False), use_container_width=True)

# =========================================================
# MODULE : FACTURATION
# =========================================================
elif page == "Nouvelle Facture":
    st.title("⚡ Créer une Facture")
    if st.session_state.facture_reussie:
        succes = st.session_state.facture_reussie
        st.balloons(); st.success(f"✅ Facture N° {succes['num']} validée !")
        c1, c2, c3 = st.columns(3)
        with c1: st.download_button("📄 Télécharger & Imprimer", data=succes['pdf'], file_name=f"Facture_{succes['num']}.pdf", mime="application/pdf", type="primary")
        with c2: st.link_button("📧 Envoyer par Email", f"mailto:?subject=Facture {succes['num']}&body=Bonjour, ci-joint votre facture.")
        with c3: 
            if st.button("🔄 Nouvelle vente"): st.session_state.facture_reussie = None; st.session_state.panier = []; st.rerun()
    else:
        c_cl, c_info = st.columns([1, 2])
        with c_cl: opt_new = "➕ Nouveau Client"; liste_cl = [opt_new] + (df_clients['nom'].tolist() if not df_clients.empty else []); choix_cl = st.selectbox("Client", liste_cl)
        info_client = {}
        with c_info:
            if choix_cl == opt_new:
                c1, c2 = st.columns(2); nom = c1.text_input("Nom *"); tel = c2.text_input("Tél"); adr = st.text_input("Adresse"); cp = c1.text_input("CP"); ville = c2.text_input("Ville")
                info_client = {"nom": nom, "telephone": tel, "adresse": adr, "code_postal": cp, "ville": ville, "id": None}
            else: cl = df_clients[df_clients['nom'] == choix_cl].iloc[0]; st.success(f"Client : {cl['nom']}"); info_client = cl.to_dict()
        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("🛞 Pneus")
            if not df_stock.empty:
                l_pneus = ["-- Choix --"] + df_stock[df_stock['stock_actuel']>0]['dimension_complete'].unique().tolist(); ch_pneu = st.selectbox("Choisir Pneu", l_pneus)
                if ch_pneu != "-- Choix --":
                    row = df_stock[df_stock['dimension_complete']==ch_pneu].iloc[0]; st.caption(f"Stock: {row['stock_actuel']} | PMP: {row['pmp_achat']:.2f}€")
                    qte = st.number_input("Qté", 1, int(row['stock_actuel']), 2, key="q_p"); px = st.number_input("Prix Vente (€)", value=float(f"{(row['pmp_achat'] or 0)*1.4:.2f}"), key="p_p")
                    if st.button("➕ Ajouter Pneu", type="secondary"): st.session_state.panier.append({"type": "PNEU", "id": int(row['id']), "desc": f"Pneu {row['marque']} {row['dimension_complete']}", "qte": qte, "prix": px, "cout": float(row['pmp_achat'] or 0)}); st.success("Ajouté !"); st.rerun()
        with col2:
            st.subheader("🔧 Services")
            if not df_services.empty:
                l_serv = ["-- Choix --"] + df_services['description'].tolist(); ch_serv = st.selectbox("Choisir Service", l_serv)
                if ch_serv != "-- Choix --":
                    s_row = df_services[df_services['description']==ch_serv].iloc[0]; qte_s = st.number_input("Qté", 1, 10, 2, key="q_s"); px_s = st.number_input("Prix (€)", value=float(s_row['prix_unitaire']), key="p_s")
                    if st.button("➕ Ajouter Service", type="secondary"): st.session_state.panier.append({"type": "SERVICE", "id": None, "desc": s_row['description'], "qte": qte_s, "prix": px_s, "cout": 0}); st.success("Ajouté !"); st.rerun()
        st.subheader("🛒 Panier")
        if st.session_state.panier:
            df_p = pd.DataFrame(st.session_state.panier); df_p['Total'] = df_p['qte'] * df_p['prix']; st.dataframe(df_p[['desc', 'qte', 'prix', 'Total']], use_container_width=True)
            col_tot, col_valid = st.columns([2, 1]); total = df_p['Total'].sum(); col_tot.metric("TOTAL A PAYER", f"{total:.2f} €")
            if col_valid.button("✅ VALIDER LA VENTE", type="primary", use_container_width=True):
                if not info_client['nom']: st.error("Client manquant !")
                else:
                    cid = info_client.get('id')
                    if not cid: cid = supabase.table('clients').insert({k:v for k,v in info_client.items() if k!='id'}).execute().data[0]['id']
                    num = f"FV-{datetime.now().strftime('%y%m-%H%M')}"; fid = supabase.table('factures_entete').insert({"client_id": cid, "total_ttc": total, "numero_facture": num, "statut": "Payée"}).execute().data[0]['id']
                    for it in st.session_state.panier:
                        supabase.table('factures_lignes').insert({"facture_id": fid, "article_id": it['id'], "quantite": it['qte'], "prix_vente_unitaire": it['prix'], "cout_achat_historique": it['cout']}).execute()
                        if it['type'] == "PNEU":
                            cur = supabase.table('articles').select('stock_actuel').eq('id', it['id']).execute().data[0]['stock_actuel']
                            supabase.table('articles').update({'stock_actuel': cur - it['qte']}).eq('id', it['id']).execute()
                            supabase.table('mouvements_stock').insert({"article_id": it['id'], "type_mouvement": "VENTE", "quantite": -it['qte'], "lien_facture_fournisseur": f"Vente {num}"}).execute()
                    pdf = generer_pdf(fid, info_client, st.session_state.panier, total, num); st.session_state.facture_reussie = {"num": num, "pdf": pdf, "client": info_client['nom']}; st.rerun()
            if st.button("🗑️ Vider Panier"): st.session_state.panier = []; st.rerun()
        else: st.info("Le panier est vide.")

elif page == "Mes Factures":
    st.title("📂 Historique Factures")
    if not df_factures.empty:
        df_disp = df_factures.copy(); df_disp['Date'] = df_disp['created_at'].apply(lambda x: x[:10]); df_disp['Client'] = df_disp['clients'].apply(lambda x: x['nom'] if x else 'Inconnu')
        st.dataframe(df_disp[['numero_facture', 'Date', 'Client', 'total_ttc', 'statut']], use_container_width=True); st.write("---")
        sel_fact = st.selectbox("Ré-imprimer facture", df_factures['numero_facture'].tolist())
        if st.button("Générer PDF"):
            row = df_factures[df_factures['numero_facture']==sel_fact].iloc[0]
            lignes_fmt = [{"desc": f"Pneu {l['articles']['marque']} {l['articles']['dimension_complete']}" if l['articles'] else "Service", "qte": l['quantite'], "prix": l['prix_vente_unitaire']} for l in get_facture_lines(row['id'])]
            pdf = generer_pdf(row['id'], row['clients'], lignes_fmt, row['total_ttc'], row['numero_facture']); st.download_button("Télécharger PDF", pdf, f"Facture_{sel_fact}.pdf", "application/pdf")

elif page == "Clients":
    st.title("👥 Base Clients")
    if not df_clients.empty:
        edited = st.data_editor(df_clients[['id', 'nom', 'telephone', 'email', 'adresse', 'ville', 'siret']], key="ed_cli", use_container_width=True, column_config={"id": st.column_config.NumberColumn(disabled=True)})
        if st.button("💾 Sauvegarder Clients"):
            for i, r in edited.iterrows(): supabase.table('clients').update({"nom": r['nom'], "telephone": r['telephone'], "email": r['email'], "adresse": r['adresse'], "ville": r['ville'], "siret": r['siret']}).eq('id', r['id']).execute()
            st.success("Clients mis à jour !"); time.sleep(1); st.rerun()

elif page == "Gestion Services":
    st.title("🔧 Services")
    with st.expander("➕ Créer"):
        with st.form("add_serv"):
            c1,c2,c3=st.columns([3,1,1]); d=c1.text_input("Nom"); p=c2.number_input("Prix",0.0,100.0,15.0); c=c3.selectbox("Type",["Montage","Pièce"])
            if st.form_submit_button("Ajouter"): supabase.table('services').insert({"description":d,"prix_unitaire":p,"categorie":c}).execute(); st.rerun()
    if not df_services.empty:
        ed_srv = st.data_editor(df_services[['id','description','prix_unitaire','categorie']], key="ed_s", use_container_width=True, column_config={"id": st.column_config.NumberColumn(disabled=True)})
        if st.button("💾 Sauvegarder Services"):
            for i,r in ed_srv.iterrows(): supabase.table('services').update({"description":r['description'],"prix_unitaire":r['prix_unitaire'],"categorie":r['categorie']}).eq('id',r['id']).execute()
            st.success("Mis à jour !"); time.sleep(1); st.rerun()

# =========================================================
# MODULE : STATISTIQUES
# =========================================================
elif page == "Chiffre d'Affaires":
    st.title("📈 CA"); df_factures['Date'] = pd.to_datetime(df_factures['created_at'])
    mode = st.radio("Vue", ["Jour", "Semaine", "Mois"], horizontal=True); rule = 'D' if mode=="Jour" else 'W' if mode=="Semaine" else 'M'
    st.plotly_chart(px.bar(df_factures.resample(rule, on='Date')['total_ttc'].sum().reset_index(), x='Date', y='total_ttc', title=f"CA par {mode}"), use_container_width=True)
    st.metric("Total", f"{df_factures['total_ttc'].sum():.2f} €")

elif page == "Top Ventes":
    st.title("🏆 Top Dimensions")
    res = supabase.table('factures_lignes').select('*, articles(*)').execute(); df_l = pd.DataFrame(res.data)
    if not df_l.empty:
        df_l['Dim'] = df_l.apply(lambda x: x['articles']['dimension_complete'] if x['articles'] else None, axis=1)
        st.plotly_chart(px.bar(df_l.dropna(subset=['Dim']).groupby('Dim')['quantite'].sum().reset_index().sort_values('quantite', ascending=False).head(10), x='quantite', y='Dim', orientation='h'), use_container_width=True)

elif page == "Valeur Stock":
    st.title("💰 Valeur Stock")
    st.metric("Total PMP", f"{(df_stock['stock_actuel'] * df_stock['pmp_achat'].fillna(0)).sum():,.2f} €")
