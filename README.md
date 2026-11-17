# BuildScope

Tableau de bord local, en lecture seule, pour suivre les déploiements Coolify et les ressources d'un serveur de compilation Colima sur macOS.

BuildScope affiche l'historique récent des applications, la recherche et le tri, ainsi que le CPU, la mémoire, l'espace disque et le cache Docker de la machine virtuelle. Lorsque le collecteur optionnel est installé, il peut mesurer la durée des commandes d'envoi d'images et estimer le trafic sortant de la VM pendant ces envois.

## Utilisation

1. Installez Python 3 et Colima sur macOS. Lancez la machine virtuelle `colima` avec Docker.
2. Créez un fichier `settings.json` dans ce dossier avec votre URL Coolify HTTPS :

   ```json
   {"coolify_url": "https://coolify.example.com"}
   ```

   Vous pouvez également définir la variable d'environnement `COOLIFY_URL`.
3. Dans Coolify, créez un jeton API à permission `read` pour les ressources à suivre. Activez l'accès API si nécessaire.
4. Lancez `python3 server.py`, puis ouvrez `http://127.0.0.1:8765/`. Sur macOS, le fichier `Lancer le suivi.command` permet également d'ouvrir le tableau de bord.
5. Saisissez le jeton dans l'interface. Il est stocké dans le Trousseau macOS et ne doit pas être ajouté aux fichiers du projet.

Le serveur écoute uniquement sur l'interface locale. Les réglages personnels et les secrets ne sont pas versionnés. Le nettoyage manuel retire du cache Docker inutilisé, en conservant jusqu'à 8 Go lorsque possible ; il est reporté si un conteneur est en cours d'exécution. Les volumes et images ne sont pas supprimés.

## Collecteur d'envoi optionnel

`collector.py` est destiné à être installé comme service dans la VM Colima, avec accès à Docker et `/proc`. Il ne stocke pas les lignes de commande complètes. Les mesures s'appliquent uniquement aux transferts observés après son démarrage. Le trafic affiché est le trafic sortant total de la VM durant la commande `docker push`, et peut inclure d'autres échanges : il ne s'agit pas de la taille exacte transférée. La taille d'image affichée est la taille locale non compressée ; les images et le cache Docker peuvent partager des données et leurs tailles ne s'additionnent pas nécessairement.

Les champs absents dans Coolify ou pour des builds effectués ailleurs restent indisponibles. La durée du déploiement inclut l'attente et le démarrage, en plus de la compilation et des transferts. L'historique est limité aux 15 derniers déploiements par application.
