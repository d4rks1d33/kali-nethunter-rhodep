<?php namespace pineapple;

require_once('DatabaseConnection.php');

class Authentication extends APIModule
{
    private $dbConnection;

    const DATABASE = "/etc/pineapple/pineapple.db";

    // Default credentials seeded at install if the auth table is empty.
    // Change these in setup (performSetup) or via the installer.
    const DEFAULT_USERNAME = "root";
    const DEFAULT_PASSWORD = "pineapplesareyummy";

    public function __construct($request)
    {
        parent::__construct($request);
        $this->dbConnection = new DatabaseConnection(self::DATABASE);
        $this->dbConnection->exec("CREATE TABLE IF NOT EXISTS api_tokens (token VARCHAR NOT NULL, name VARCHAR NOT NULL);");
        // Port: authenticate against our own credential store (SQLite) with a
        // modern password hash (password_hash/password_verify), NOT /etc/shadow.
        $this->dbConnection->exec("CREATE TABLE IF NOT EXISTS auth (username VARCHAR NOT NULL UNIQUE, password_hash VARCHAR NOT NULL);");
        $this->ensureDefaultCredentials();
    }

    private function ensureDefaultCredentials()
    {
        $rows = $this->dbConnection->query("SELECT COUNT(*) AS c FROM auth;");
        if (!empty($rows) && isset($rows[0]['c']) && intval($rows[0]['c']) === 0) {
            $hash = password_hash(self::DEFAULT_PASSWORD, PASSWORD_DEFAULT);
            $this->dbConnection->exec(
                "INSERT INTO auth(username, password_hash) VALUES('%s','%s');",
                self::DEFAULT_USERNAME,
                $hash
            );
        }
    }

    public function getApiTokens()
    {
        $this->response = array("tokens" => $this->dbConnection->query("SELECT token,name FROM api_tokens;"));
    }

    public function checkApiToken()
    {
        if (isset($this->request->token)) {
            $token = $this->request->token;
            $result = $this->dbConnection->query("SELECT token FROM api_tokens WHERE token='%s';", $token);
            if (!empty($result) && isset($result[0]["token"]) && $result[0]["token"] === $token) {
                $this->response = array("valid" => true);
                return;
            }
        }
        $this->response = array("valid" => false);
    }

    public function addApiToken()
    {
        if (isset($this->request->token) && isset($this->request->name)) {
            $token = $this->request->token;
            $name = $this->request->name;
            $this->dbConnection->exec("INSERT INTO api_tokens(token, name) VALUES('%s','%s');", $token, $name);
            $this->response = array("success" => true);
            return;
        }
        $this->error = "Missing token or name";
    }

    private function login()
    {
        if (isset($this->request->username) && isset($this->request->password)) {
            if ($this->verifyPassword($this->request->password)) {
                $_SESSION['logged_in'] = true;
                $this->response = array("logged_in" => true);
                if (!isset($this->request->time)) {
                    return;
                }
                $epoch = intval($this->request->time);
                if ($epoch > 1) {
                    exec('date -s @' . $epoch);
                }
                return;
            }
        }

        $this->response = array("logged_in" => false);
    }

    private function verifyPassword($password)
    {
        // Port: verify against the SQLite auth store using password_verify
        // (bcrypt/argon2 via PASSWORD_DEFAULT), instead of crypt() on /etc/shadow.
        $username = isset($this->request->username) ? $this->request->username : self::DEFAULT_USERNAME;
        $result = $this->dbConnection->query(
            "SELECT password_hash FROM auth WHERE username='%s';",
            $username
        );
        if (empty($result) || !isset($result[0]['password_hash'])) {
            return false;
        }
        $hash = $result[0]['password_hash'];
        if (!is_string($hash) || $hash === "") {
            return false;
        }
        return password_verify($password, $hash);
    }

    private function logout()
    {
        $this->response = array("logged_in" => false);
        unset($_COOKIE['XSRF-TOKEN']);
        setcookie('XSRF-TOKEN', '', time()-3600);
        unset($_SESSION['XSRF-TOKEN']);
        unset($_SESSION['logged_in']);
        session_destroy();
    }

    private function checkAuth()
    {
        if (isset($_SESSION['logged_in']) && $_SESSION['logged_in'] === true) {
            $this->response = array("authenticated" => true);
        } else {
            if (file_exists("/etc/pineapple/setupRequired")) {
                $this->response = array("error" => "Not Authenticated", "setupRequired" => true);
            } else {
                $this->response = array("error" => "Not Authenticated");
            }
        }
    }

    public function route()
    {
        switch ($this->request->action) {
            case 'login':
                $this->login();
                break;

            case 'logout':
                $this->logout();
                break;

            case 'checkAuth':
                $this->checkAuth();
                break;

            case 'checkApiToken':
                $this->checkApiToken();
                break;

            case 'addApiToken':
                $this->addApiToken();
                break;

            case 'getApiTokens':
                $this->getApiTokens();
                break;

            default:
                $this->error = "Unknown action";
        }
        
        session_write_close();
    }
}
