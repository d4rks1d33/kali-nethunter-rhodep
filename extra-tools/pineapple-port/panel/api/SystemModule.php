<?php namespace pineapple;

require_once('DatabaseConnection.php');

abstract class SystemModule extends Module
{
    // Port: password changes target our own SQLite credential store with a
    // modern hash (password_hash/password_verify), NOT /etc/shadow.
    const AUTH_DATABASE = "/etc/pineapple/pineapple.db";
    const AUTH_DEFAULT_USERNAME = "root";

    protected function changePassword($current, $new)
    {
        $db = new DatabaseConnection(self::AUTH_DATABASE);
        $db->exec("CREATE TABLE IF NOT EXISTS auth (username VARCHAR NOT NULL UNIQUE, password_hash VARCHAR NOT NULL);");

        $username = self::AUTH_DEFAULT_USERNAME;
        $result = $db->query("SELECT password_hash FROM auth WHERE username='%s';", $username);
        if (empty($result) || !isset($result[0]['password_hash'])) {
            return false;
        }

        $hash = $result[0]['password_hash'];
        if (!is_string($hash) || !password_verify($current, $hash)) {
            return false;
        }

        $newHash = password_hash($new, PASSWORD_DEFAULT);
        $db->exec("UPDATE auth SET password_hash='%s' WHERE username='%s';", $newHash, $username);

        return true;
    }
}
