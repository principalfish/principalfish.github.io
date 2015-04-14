// http://bl.ocks.org/mbostock/1093025
// http://mbostock.github.io/d3/talk/20111018/treemap.html
// http://bl.ocks.org/mbostock/4339083

var chess_data = []
d3.json("/projects/chessopeningexplorer/info.json", function(error, data) {
  chess_data = data
  main()
})

function main(){
  console.log(chess_data)

  display(chess_data["start"]["m"])
}

active_state = []

function display(data){
  $.each(data, function(i){
    console.log(data[i])
    $("#main").append("<div class = \"button\">" + i
                            + " white: " + data[i]["c"]["white"]
                            + " black: " + data[i]["c"]["black"]
                            + " draw: " + data[i]["c"]["draw"] + "</div>")
  })
}
