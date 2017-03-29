var choro = {

  keyOnMap: function(){

    $("#keyonmap").show();

    var increment = 3

    // add key twice including negative for vote share

    $("#keyonmap").append("<div class='leave'>Leave</div>")
    for (var i=5; i > 0; i--){
      var background = "rgba(0, 0, 255, " + i * 0.2 + ")";

      var toAdd = "<div class='choro-minus' style='background-color: " + background + "'>"
                  +  (50 + i * increment).toFixed(1) + "%";

      if (i == 5){
        toAdd += "+";
      }

      toAdd += "</div>";

      $("#keyonmap").append(toAdd);
    }


    for (var i=0; i < 6; i++){

      var background = "rgba(255, 215, 0, " + i * 0.2 + ")";

      var toAdd = "<div class='' style='background-color:" + background + "'>" +
      (50 + i * increment).toFixed(1) + "%";

      if (i == 5){
        toAdd += "+"
      }
      toAdd += "</div>";

      $("#keyonmap").append(toAdd);
    }
      $("#keyonmap").append("<div class='remain'>Remain</div>")
  }


};
