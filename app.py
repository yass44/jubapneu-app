import streamlit as st
import pandas as pd
import plotly.express as px
from supabase import create_client, Client
from datetime import datetime
import io
import re
import pdfplumber
import os
import time

# Imports pour le PDF Pro
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from textwrap import wrap

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
# 🔒 AUTHENTIFICATION
# =========================================================
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False
    if st.session_state.get("password_correct", False): return True
    st.title("🔒 Accès Sécurisé JubaPneu")
    st.text_input("Mot de passe :", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state and not st.session_state["password_correct"]: st.error("❌ Incorrect.")
    return False

if not check_password(): st.stop()

# =========================================================
# ✅ APP
# =========================================================
@st.cache_resource
def init_connection():
    try:
        try:
            import secrets_config; local_url = secrets_config.SUPABASE_URL; local_key = secrets_config.SUPABASE_KEY
        except ImportError: local_url = None; local_key = None
        if "SUPABASE_URL" in st.secrets: url = st.secrets["SUPABASE_URL"]; key = st.secrets["SUPABASE_KEY"]
        elif local_url: url = local_url; key = local_key
        else: st.error("Clés introuvables."); return None
        return create_client(url, key)
    except Exception as e: st.error(f"Erreur : {e}"); return None

supabase = init_connection()

# --- DATA ---
def load_all_data():
    if not supabase: return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    stock = pd.DataFrame(supabase.table('articles').select("*").execute().data)
    mouv = pd.DataFrame(supabase.table('mouvements_stock').select("*").execute().data)
    clients = pd.DataFrame(supabase.table('clients').select("*").order('nom').execute().data)
    factures = pd.DataFrame(supabase.table('factures_entete').select('*, clients(*)').order('created_at', desc=True).execute().data)
    services = pd.DataFrame(supabase.table('services').select("*").order('description').execute().data)
    return stock, mouv, clients, factures, services

def get_facture_lines(facture_id):
    return supabase.table('factures_lignes').select('*, articles(*)').eq('facture_id', facture_id).execute().data

# --- GÉNÉRATEUR PDF (PRO) ---
def generer_pdf(facture_id, client_dict, lignes, total_ttc, numero_facture, date_obj=None):
    import os # IMPORT DE SÉCURITÉ PLACÉ ICI
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Gestion de la date
    if not date_obj: date_str = datetime.now().strftime('%d/%m/%Y')
    elif isinstance(date_obj, str): date_str = date_obj 
    else: date_str = date_obj.strftime('%d/%m/%Y')

    # --- 1. EN-TÊTE GAUCHE (Logo & Entreprise) ---
   import os
    dossier_actuel = os.path.dirname(os.path.abspath(__file__))
    chemin_logo = os.path.join(dossier_actuel, "logo.png")

    if os.path.exists(chemin_logo):
        try:
            # CORRECTION ICI : Ajout de height=60 pour que l'image apparaisse !
            c.drawImage(chemin_logo, 50, height - 110, width=140, height=60, preserveAspectRatio=True, mask='auto')
        except Exception as e:
            c.setFont("Helvetica-Bold", 20)
            c.drawString(50, height - 50, "JUBAPNEU")
    else:
        c.setFont("Helvetica-Bold", 20)
        c.drawString(50, height - 50, "JUBAPNEU")

    c.setFont("Helvetica", 10)
    y_info = height - 120 
    c.drawString(50, y_info, "10 Place Jeanne d'Arc - 54310 Homécourt")
    c.drawString(50, y_info - 15, "Tel: 09 54 45 98 22")
    c.drawString(50, y_info - 30, "Email: contact@jubapneu.eu | Web: jubapneu.eu")
    
    # --- 2. EN-TÊTE DROITE (Numéro Facture) ---
    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(width - 50, height - 50, "FACTURE")
    c.setFont("Helvetica", 12)
    c.drawRightString(width - 50, height - 70, f"N° {numero_facture}")
    c.setFont("Helvetica", 10)
    c.drawRightString(width - 50, height - 90, f"Le {date_str}")
    
    # --- 3. BLOC CLIENT ---
    c.roundRect(width - 250, height - 200, 200, 75, 5)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(width - 240, height - 145, f"Client : {client_dict.get('nom', 'Inconnu')}")
    c.setFont("Helvetica", 10)
    if client_dict.get('adresse'): 
        c.drawString(width - 240, height - 160, f"{client_dict['adresse']}")
        c.drawString(width - 240, height - 175, f"{client_dict.get('code_postal', '')} {client_dict.get('ville', '')}")
    
    # --- 4. TABLEAU DES LIGNES ---
    data = [["Désignation", "Qté", "TVA", "Montant HT", "Montant TTC"]]
    
    total_ht = 0
    total_tva = 0
    
    for l in lignes:
        desc = l.get('desc') or (f"Pneu {l['articles']['marque']} {l['articles']['dimension_complete']}" if l.get('articles') else "Service")
        qte = l.get('qte') or l.get('quantite')
        prix_ttc_unit = l.get('prix') or l.get('prix_vente_unitaire')
        
        prix_ttc_total = qte * prix_ttc_unit
        prix_ht_total = prix_ttc_total / 1.20
        tva_total = prix_ttc_total - prix_ht_total
        
        total_ht += prix_ht_total
        total_tva += tva_total
        
        desc_wrapped = "\n".join(wrap(desc, 45)) 
        
        data.append([
            desc_wrapped, 
            str(qte), 
            "20%", 
            f"{prix_ht_total:.2f} €", 
            f"{prix_ttc_total:.2f} €"
        ])
        
    table = Table(data, colWidths=[230, 40, 40, 80, 80])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#f2f2f2")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.black),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('ALIGN', (0,1), (0,-1), 'LEFT'),   
        ('ALIGN', (3,1), (-1,-1), 'RIGHT'), 
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey)
    ]))
    
    table.wrapOn(c, width, height)
    w, h = table.wrap(0, 0)
    y_pos = height - 240 - h
    table.drawOn(c, 50, y_pos)
    
    # --- 5. TOTAUX ---
    y_tot = y_pos - 30
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(width - 140, y_tot, "Total HT :")
    c.drawRightString(width - 50, y_tot, f"{total_ht:.2f} €")
    
    y_tot -= 15
    c.setFont("Helvetica", 10)
    c.drawRightString(width - 140, y_tot, "TVA (20%) :")
    c.drawRightString(width - 50, y_tot, f"{total_tva:.2f} €")
    
    y_tot -= 20
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(width - 140, y_tot, "Total TTC :")
    c.drawRightString(width - 50, y_tot, f"{total_ttc:.2f} €")
    
    # --- 6. CONDITIONS DE PAIEMENT ---
    c.setFont("Helvetica", 9)
    c.drawString(50, y_tot, "Conditions de paiement :")
    c.drawString(50, y_tot - 15, f"• 100,00% soit {total_ttc:.2f} € à payer comptant.")
    
    # --- 7. PIED DE PAGE LÉGAL ---
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.dimgrey)
    c.drawCentredString(width / 2, 40, "SARL - Société à Responsabilité Limitée JubaPneu au capital social de 10 000€")
    c.drawCentredString(width / 2, 30, "Siège social: 10 Place Jeanne d'Arc-54310 Homécourt")
    c.drawCentredString(width / 2, 20, "Siret: 92063340100012 | Numéro TVA Intracommunautaire: FR09920633401")
    
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

# --- ANALYSE PDF ---
def analyser_ligne_deldo(description):
    infos = {"valid": False, "dimension_complete": description, "largeur": None, "hauteur": None, "diametre": None, "charge": "", "vitesse": "", "saison": "Été", "marque": "Inconnue"}
    regex = r"(\d{3})\s+(\d{2})\s+[A-Z]+\s+(\d{2})\s+(\d{2,3})\s+([A-Z])\s"; match = re.search(regex, description)
    if match:
        infos["valid"]=True; infos["largeur"]=int(match.group(1)); infos["hauteur"]=int(match.group(2)); infos["diametre"]=int(match.group(3))
        infos["charge"]=match.group(4); infos["vitesse"]=match.group(5)
        infos["dimension_complete"] = f"{infos['largeur']}/{infos['hauteur']} R{infos['diametre']} {infos['charge']}{infos['vitesse']}"
    else: return infos
    desc_up = description.upper()
    if "AS" in desc_up or "4S" in desc_up or "ALL" in desc_up: infos["saison"]="4 Saisons"
    elif "WINTER" in desc_up or "HIVER" in desc_up: infos["saison"]="Hiver"
    mots = description.split(); 
    if mots: infos["marque"] = mots[0]
    return infos

# --- INIT ---
df_stock, df_hist, df_clients, df_factures, df_services = load_all_data()
if 'panier' not in st.session_state: st.session_state.panier = []
if 'facture_reussie' not in st.session_state: st.session_state.facture_reussie = None

# =========================================================
# NAVIGATION
# =========================================================
st.sidebar.title("🗄️ JubaPneu")
tiroir = st.sidebar.selectbox("Module :", ["📦 STOCK", "💰 FACTURATION", "📊 STATISTIQUES"])
st.sidebar.markdown("---")
page=""
if tiroir == "📦 STOCK": page = st.sidebar.radio("Nav", ["Stock Actuel", "📥 Importer Facture Fournisseur", "Historique Mouvements"])
elif tiroir == "💰 FACTURATION": page = st.sidebar.radio("Nav", ["Nouvelle Facture", "Mes Factures", "Clients", "Gestion Services"])
elif tiroir == "📊 STATISTIQUES": page = st.sidebar.radio("Nav", ["Chiffre d'Affaires", "Top Ventes", "Valeur Stock"])

# =========================================================
# STOCK
# =========================================================
if page == "Stock Actuel":
    st.title("📦 Stock")
    if not df_stock.empty:
        df_view = df_stock[df_stock['stock_actuel'] > 0].copy()
        c1, c2 = st.columns([2,1]); search = c1.text_input("🔍", placeholder="Recherche..."); saisons = c2.multiselect("Saison", df_view['saison'].unique())
        if search: df_view = df_view[df_view.astype(str).apply(lambda x: x.str.contains(search, case=False)).any(axis=1)]
        if saisons: df_view = df_view[df_view['saison'].isin(saisons)]
        st.dataframe(df_view[['dimension_complete', 'stock_actuel', 'marque', 'saison', 'charge', 'vitesse', 'pmp_achat']], column_config={"stock_actuel": st.column_config.NumberColumn("Stock", format="%d"), "pmp_achat": st.column_config.NumberColumn("PMP", format="%.2f €")}, use_container_width=True, height=600)
    else: st.info("Stock vide.")

elif page == "📥 Importer Facture Fournisseur":
    st.title("📥 Import Deldo")
    f = st.file_uploader("PDF", type="pdf")
    if f:
        st.write("Analyse..."); found = []
        with pdfplumber.open(f) as pdf:
            for p in pdf.pages:
                txt = p.extract_text() or ""
                for l in txt.split('\n'):
                    m = re.search(r"^(\d+)\s+(.+)\s+(\d+\.\d{2})\s+(\d+\.\d{2})$", l.strip())
                    if m:
                        inf = analyser_ligne_deldo(m.group(2))
                        if inf["valid"]: found.append({"Desc": inf['dimension_complete'], "Marque": inf['marque'], "Qté": int(m.group(1)), "Prix": float(m.group(3)), "_inf": inf})
        if found:
            st.dataframe(pd.DataFrame(found)[['Desc', 'Marque', 'Qté', 'Prix']], use_container_width=True)
            if st.button("🚀 VALIDER L'IMPORT"):
                bar = st.progress(0)
                for i, it in enumerate(found):
                    inf = it['_inf']; q = it['Qté']; p = it['Prix']
                    now_iso = datetime.now().isoformat()
                    
                    res = supabase.table('articles').select("*").eq('dimension_complete', inf['dimension_complete']).eq('marque', inf['marque']).execute()
                    if res.data:
                        ex = res.data[0]; old_s = ex['stock_actuel']; old_p = float(ex['pmp_achat'] or 0)
                        ns = old_s + q; np = ((old_s * old_p) + (q * p)) / ns if ns > 0 else p
                        supabase.table('articles').update({'stock_actuel': ns, 'pmp_achat': np}).eq('id', ex['id']).execute(); aid = ex['id']
                    else:
                        new = {'dimension_complete': inf['dimension_complete'], 'largeur': inf['largeur'], 'hauteur': inf['hauteur'], 'diametre': inf['diametre'], 'charge': inf['charge'], 'vitesse': inf['vitesse'], 'marque': inf['marque'], 'saison': inf['saison'], 'stock_actuel': q, 'pmp_achat': p}
                        aid = supabase.table('articles').insert(new).execute().data[0]['id']
                    
                    supabase.table('mouvements_stock').insert({'article_id': aid, 'type_mouvement': 'ACHAT', 'quantite': q, 'prix_achat_unitaire': p, 'lien_facture_fournisseur': f.name, 'created_at': now_iso}).execute()
                    bar.progress((i+1)/len(found))
                st.success("Importé !"); st.cache_resource.clear(); time.sleep(2); st.rerun()
        else: st.warning("Rien trouvé.")

elif page == "Historique Mouvements":
    st.title("📜 Historique")
    if not df_hist.empty:
        df_hist['created_at'] = pd.to_datetime(df_hist['created_at'], errors='coerce')
        st.dataframe(df_hist.dropna(subset=['created_at']).sort_values('created_at', ascending=False), use_container_width=True)

# =========================================================
# FACTURATION
# =========================================================
elif page == "Nouvelle Facture":
    st.title("⚡ Facture")
    if st.session_state.facture_reussie:
        s = st.session_state.facture_reussie; st.balloons(); st.success(f"Facture {s['num']} OK !")
        c1,c2,c3 = st.columns(3)
        with c1: st.download_button("📄 PDF", s['pdf'], f"Facture_{s['num']}.pdf", "application/pdf", type="primary")
        with c2: st.link_button("📧 Email", f"mailto:?subject=Facture {s['num']}&body=Ci-joint votre facture.")
        with c3: 
            if st.button("🔄 Nouveau"): st.session_state.facture_reussie=None; st.session_state.panier=[]; st.rerun()
    else:
        c1, c2 = st.columns([1,2]); opt="➕ Nouveau"; lst=[opt]+(df_clients['nom'].tolist() if not df_clients.empty else []); ch=c1.selectbox("Client", lst); cli={}
        if ch==opt: 
            cc1,cc2=c2.columns(2); nm=cc1.text_input("Nom*"); tl=cc2.text_input("Tél"); ad=c2.text_input("Adresse"); cp=cc1.text_input("CP"); vi=cc2.text_input("Ville")
            cli={"nom":nm, "telephone":tl, "adresse":ad, "code_postal":cp, "ville":vi, "id":None}
        else: r=df_clients[df_clients['nom']==ch].iloc[0]; st.success(f"Client : {r['nom']}"); cli=r.to_dict()
        
        st.divider(); ca, cb = st.columns(2)
        with ca:
            st.subheader("🛞 Pneu")
            if not df_stock.empty:
                lp=["--"]+df_stock[df_stock['stock_actuel']>0]['dimension_complete'].unique().tolist(); cp=st.selectbox("Réf", lp)
                if cp!="--":
                    r=df_stock[df_stock['dimension_complete']==cp].iloc[0]; st.caption(f"Stock: {r['stock_actuel']}")
                    q=st.number_input("Qté", 1, int(r['stock_actuel']), 2, key="qp"); px=st.number_input("Prix", value=float(f"{(r['pmp_achat'] or 0)*1.4:.2f}"), key="pp")
                    if st.button("➕ Ajout Pneu"): st.session_state.panier.append({"type":"PNEU", "id":int(r['id']), "desc":f"Pneu {r['marque']} {r['dimension_complete']}", "qte":q, "prix":px, "cout":float(r['pmp_achat'] or 0)}); st.rerun()
        with cb:
            st.subheader("🔧 Service")
            if not df_services.empty:
                ls=["--"]+df_services['description'].tolist(); cs=st.selectbox("Svc", ls)
                if cs!="--":
                    rs=df_services[df_services['description']==cs].iloc[0]; qs=st.number_input("Qté",1,10,2,key="qs"); ps=st.number_input("Prix",value=float(rs['prix_unitaire']),key="ps")
                    if st.button("➕ Ajout Svc"): st.session_state.panier.append({"type":"SERVICE", "id":None, "desc":rs['description'], "qte":qs, "prix":ps, "cout":0}); st.rerun()

        st.subheader("🛒 Panier")
        if st.session_state.panier:
            dfp=pd.DataFrame(st.session_state.panier); dfp['Tot']=dfp['qte']*dfp['prix']; st.dataframe(dfp[['desc','qte','prix','Tot']], use_container_width=True)
            ct, cv = st.columns([2,1]); tot=dfp['Tot'].sum(); ct.metric("Total", f"{tot:.2f} €")
            if cv.button("✅ VALIDER", type="primary"):
                if not cli['nom']: st.error("Nom client !")
                else:
                    cid=cli.get('id')
                    if not cid: cid=supabase.table('clients').insert({k:v for k,v in cli.items() if k!='id'}).execute().data[0]['id']
                    num=f"FV-{datetime.now().strftime('%y%m-%H%M')}"; fid=supabase.table('factures_entete').insert({"client_id":cid, "total_ttc":tot, "numero_facture":num, "statut":"Payée"}).execute().data[0]['id']
                    for it in st.session_state.panier:
                        supabase.table('factures_lignes').insert({"facture_id":fid, "article_id":it['id'], "quantite":it['qte'], "prix_vente_unitaire":it['prix'], "cout_achat_historique":it['cout']}).execute()
                        if it['type']=="PNEU":
                            cur=supabase.table('articles').select('stock_actuel').eq('id',it['id']).execute().data[0]['stock_actuel']
                            supabase.table('articles').update({'stock_actuel':cur-it['qte']}).eq('id',it['id']).execute()
                            supabase.table('mouvements_stock').insert({"article_id":it['id'], "type_mouvement":"VENTE", "quantite":-it['qte'], "lien_facture_fournisseur":f"Vente {num}", "created_at":datetime.now().isoformat()}).execute()
                    pdf=generer_pdf(fid, cli, st.session_state.panier, tot, num); st.session_state.facture_reussie={"num":num, "pdf":pdf, "client":cli['nom']}; st.rerun()
            if st.button("🗑️ Vider"): st.session_state.panier=[]; st.rerun()

elif page == "Mes Factures":
    st.title("📂 Factures")
    if not df_factures.empty:
        df_factures['created_at'] = pd.to_datetime(df_factures['created_at'], errors='coerce')
        dfd=df_factures.copy(); dfd['Date']=dfd['created_at'].dt.strftime('%d/%m/%Y'); dfd['Client']=dfd['clients'].apply(lambda x:x['nom'] if x else '?')
        st.dataframe(dfd[['numero_facture','Date','Client','total_ttc']], use_container_width=True)
        sel=st.selectbox("Imprimer", df_factures['numero_facture'].tolist())
        if st.button("PDF"):
            r=df_factures[df_factures['numero_facture']==sel].iloc[0]
            ls=[{"desc":f"Pneu {l['articles']['marque']} {l['articles']['dimension_complete']}" if l['articles'] else "Svc", "qte":l['quantite'], "prix":l['prix_vente_unitaire']} for l in get_facture_lines(r['id'])]
            st.download_button("Télécharger", generer_pdf(r['id'], r['clients'], ls, r['total_ttc'], r['numero_facture'], r['created_at']), f"Facture_{sel}.pdf", "application/pdf")

elif page == "Clients":
    st.title("👥 Clients")
    if not df_clients.empty:
        ed=st.data_editor(df_clients[['id','nom','telephone','email','adresse','ville','siret']], key="edc", column_config={"id":st.column_config.NumberColumn(disabled=True)}, use_container_width=True)
        if st.button("💾 Save"):
            for i,r in ed.iterrows(): supabase.table('clients').update({"nom":r['nom'],"telephone":r['telephone'],"email":r['email'],"adresse":r['adresse'],"ville":r['ville'],"siret":r['siret']}).eq('id',r['id']).execute()
            st.success("OK"); time.sleep(1); st.rerun()

elif page == "Gestion Services":
    st.title("🔧 Services")
    with st.expander("➕"):
        with st.form("ads"):
            c1,c2=st.columns([3,1]); d=c1.text_input("Nom"); p=c2.number_input("Prix",0.0,100.0,15.0)
            if st.form_submit_button("Ok"): supabase.table('services').insert({"description":d,"prix_unitaire":p,"categorie":"Montage"}).execute(); st.rerun()
    if not df_services.empty:
        eds=st.data_editor(df_services[['id','description','prix_unitaire','categorie']], key="eds", column_config={"id":st.column_config.NumberColumn(disabled=True)}, use_container_width=True)
        if st.button("💾 Save Svc"):
            for i,r in eds.iterrows(): supabase.table('services').update({"description":r['description'],"prix_unitaire":r['prix_unitaire'],"categorie":r['categorie']}).eq('id',r['id']).execute()
            st.success("OK"); time.sleep(1); st.rerun()

# =========================================================
# STATS
# =========================================================
elif page == "Chiffre d'Affaires":
    st.title("📈 CA")
    if not df_factures.empty:
        df_factures['created_at'] = pd.to_datetime(df_factures['created_at'], errors='coerce')
        mode=st.radio("Vue",["Jour","Semaine","Mois"], horizontal=True); r='D' if mode=="Jour" else 'W' if mode=="Semaine" else 'M'
        ch=df_factures.resample(r, on='created_at')['total_ttc'].sum().reset_index()
        st.plotly_chart(px.bar(ch, x='created_at', y='total_ttc', title=f"CA ({mode})"), use_container_width=True)
        st.metric("Total", f"{df_factures['total_ttc'].sum():.2f} €")

elif page == "Top Ventes":
    st.title("🏆 Top")
    res=supabase.table('factures_lignes').select('*, articles(*)').execute(); dfl=pd.DataFrame(res.data)
    if not dfl.empty:
        dfl['Dim']=dfl.apply(lambda x:x['articles']['dimension_complete'] if x['articles'] else None, axis=1)
        st.plotly_chart(px.bar(dfl.dropna(subset=['Dim']).groupby('Dim')['quantite'].sum().reset_index().sort_values('quantite', ascending=False).head(10), x='quantite', y='Dim', orientation='h'), use_container_width=True)

elif page == "Valeur Stock":
    st.title("💰 Stock Value")
    st.metric("Total", f"{(df_stock['stock_actuel']*df_stock['pmp_achat'].fillna(0)).sum():,.2f} €")


