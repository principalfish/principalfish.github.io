import connectdb

cursor = connectdb.cur
connection = connectdb.db

#execute commands


#create table
def create_table(table_name, columns_array):
    command = "CREATE TABLE IF NOT EXISTS " + table_name + " ("

    for i in range(len(columns_array)):
        command += columns_array[i]
        if i != len(columns_array):
            command += ", "

    command = command[:-2]

    command += ");"

    cursor.execute(command)

#insert into
def insert_into(table_name, column_names, values):
    command = "INSERT INTO " + table_name + " (" + column_names + ") VALUES (" + values + ");"
    print command
    cursor.execute(command)
    connection.commit()
